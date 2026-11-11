/*
 * ui/user/preview-refresh.js: when the User page's thumbnails are fetched.
 *
 * A picture is loaded once when its card first scrolls into view, and then kept current: every
 * five seconds while the page is showing and the card is on screen and has a picture, and at once
 * when a route finishes. That is all this decides. How a picture is fetched, and how it is drawn,
 * belong to the page and are passed in, so this can be driven with a fake clock.
 *
 * What it holds to:
 *  - one timer for the whole page, started only while something visible has a picture and the
 *    page is showing, and cleared the moment that stops being true;
 *  - at most three pictures being fetched at once, however many sources there are: a browser keeps
 *    about six connections to a server, and a route or a Matrix read must always find one free;
 *  - one request per source in flight, however many cards show it. A tick never queues behind a
 *    slow device and a slow device never gathers a backlog: the tick that finds a request still
 *    out simply does not ask again;
 *  - a card whose preview is switched off at the device is not polled again. One that merely did
 *    not answer is retried slowly, every sixth tick, rather than abandoned or hammered;
 *  - a route that completes refreshes what is on screen without waiting for the next tick, unless
 *    a request begun after that route is already out -- it describes the world as it now is;
 *  - leaving the page, signing out or the session ending stops everything.
 *
 * It is not a poller of devices: each request goes to the server's own preview proxy, which holds
 * a picture for five seconds, so a room full of pages is one device fetch per source per interval.
 */
(function (root) {
  'use strict';

  var INTERVAL_MS = 5000;
  var RETRY_EVERY_TICKS = 6;   // an unanswered preview is retried about every thirty seconds
  // A browser keeps about six connections open to one server, and a picture is the slowest thing
  // this page asks for. Measured with fourteen sources whose pictures each took two seconds: the
  // pictures filled every connection and the Matrix read -- 0.4 ms of work on the server -- waited
  // 1.8 s behind them. So only this many pictures are in flight at once, and the rest wait their
  // turn; a route, a Matrix read or a permission check always has a connection to use.
  var MAX_CONCURRENT_FETCHES = 3;

  function create(deps) {
    var tracked = new Map();    // shot -> {id, visible, state: 'unknown'|'live'|'disabled'|'unavailable'}
    var inflight = new Map();   // source id -> {startedAt, dirty}
    var timer = null;
    var stopped = false;
    var hidden = !!(deps.hidden && deps.hidden());
    var stats = {ticks: 0, requests: 0, joined: 0, timersStarted: 0};
    var active = 0;             // pictures actually being fetched
    var waiting = [];           // started later, in the order they were asked for
    var NOT_FETCHED = {image: null, reason: 'unavailable', note: ''};

    // Runs `work` when fewer than MAX_CONCURRENT_FETCHES pictures are being fetched. A picture that
    // was queued and is no longer wanted -- the page was left, the user signed out -- is answered
    // without being fetched, so stopping never leaves a request waiting for a turn that is not coming.
    function limited(work) {
      return new Promise(function (resolve) {
        function run() {
          active += 1;
          Promise.resolve()
            .then(function () { return stopped ? NOT_FETCHED : work(); })
            .catch(function () { return NOT_FETCHED; })
            .then(function (result) {
              active -= 1;
              var next = waiting.shift();
              if (next) next();
              resolve(result);
            });
        }
        if (active < MAX_CONCURRENT_FETCHES) run(); else waiting.push(run);
      });
    }

    function eligibleIds(includeRetries) {
      var ids = new Set();
      tracked.forEach(function (entry) {
        if (!entry.visible) return;
        if (entry.state === 'live' || (includeRetries && entry.state === 'unavailable')) ids.add(entry.id);
      });
      return ids;
    }

    // The timer exists exactly while there is something for it to do.
    function sync() {
      var want = !stopped && !hidden && (eligibleIds(true).size > 0);
      if (want && timer === null) {
        timer = deps.setInterval(tick, INTERVAL_MS);
        stats.timersStarted += 1;
      } else if (!want && timer !== null) {
        deps.clearInterval(timer);
        timer = null;
      }
    }

    function tick() {
      if (stopped || hidden) return;
      stats.ticks += 1;
      eligibleIds(stats.ticks % RETRY_EVERY_TICKS === 0).forEach(function (id) { request(id); });
    }

    function request(id, opts) {
      var current = inflight.get(id);
      if (current) {
        stats.joined += 1;
        if (opts && opts.dirty) current.dirty = true;
        return current.promise;
      }
      var record = {startedAt: deps.now(), dirty: false, promise: null};
      inflight.set(id, record);
      stats.requests += 1;

      function settle(result) {
        // Applied to every card showing this source, and only to cards that are still on the page:
        // a list rebuilt while the request was out has already dropped the old ones.
        tracked.forEach(function (entry, shot) {
          if (entry.id !== id) return;
          deps.apply(shot, result);
          entry.state = result && result.image ? 'live'
            : (result && result.reason === 'preview_disabled' ? 'disabled' : 'unavailable');
        });
      }

      // However it ends -- a failed fetch, a card that cannot be drawn on -- the request is released,
      // or one failure would leave that source unable to be asked for again.
      record.promise = Promise.resolve()
        .then(function () { return limited(function () { return deps.fetchPreview(id); }); })
        .catch(function () { return {image: null, reason: 'unavailable', note: ''}; })
        .then(function (result) { try { settle(result); } catch (err) { /* drawing failed; the request is still over */ } })
        .then(function () {
          inflight.delete(id);
          sync();
          if (record.dirty && !stopped && !hidden) request(id);
        });
      return record.promise;
    }

    return {
      // A card that shows a picture of `id`. `visible` is whether it is on screen right now; with no
      // observer to ask, the page says true and every card counts.
      track: function (shot, id, visible) {
        if (stopped || !shot || !id) return;
        tracked.set(shot, {id: id, visible: visible !== false, state: 'unknown'});
      },
      // The first load of a card, on its way into view.
      load: function (shot) {
        var entry = tracked.get(shot);
        if (!entry || stopped) return Promise.resolve();
        return request(entry.id);
      },
      setVisible: function (shot, visible) {
        var entry = tracked.get(shot);
        if (!entry) return;
        entry.visible = !!visible;
        sync();
      },
      // The list was rebuilt: every card tracked belonged to the old one.
      reset: function () { tracked.clear(); sync(); },
      // The page was hidden or shown. Coming back refreshes at once what has been standing still.
      setHidden: function (value) {
        hidden = !!value;
        if (!hidden && !stopped) eligibleIds(true).forEach(function (id) { request(id); });
        sync();
      },
      // A route finished at `at`. Whatever is on screen is refreshed now, including a card that had no
      // picture before and may have one now.
      routeCompleted: function (at) {
        if (stopped || hidden) return;
        var ids = new Set();
        tracked.forEach(function (entry) { if (entry.visible) ids.add(entry.id); });
        ids.forEach(function (id) {
          var current = inflight.get(id);
          if (current && current.startedAt >= at) return;       // already describes the world after the route
          request(id, {dirty: true});
        });
      },
      stop: function () {
        stopped = true;
        tracked.clear();
        sync();
        // Everything waiting for a turn is released now, answered without a fetch.
        var queued = waiting.splice(0);
        queued.forEach(function (run) { run(); });
      },
      stats: function () {
        return {ticks: stats.ticks, requests: stats.requests, joined: stats.joined, timersStarted: stats.timersStarted,
                timers: timer === null ? 0 : 1, inflight: inflight.size, tracked: tracked.size,
                fetching: active, waiting: waiting.length};
      },
    };
  }

  root.OmniUserPreviews = {create: create, INTERVAL_MS: INTERVAL_MS, RETRY_EVERY_TICKS: RETRY_EVERY_TICKS,
                           MAX_CONCURRENT_FETCHES: MAX_CONCURRENT_FETCHES};
})(typeof window !== 'undefined' ? window : this);
