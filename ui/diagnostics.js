/*
 * Development diagnostics. Off unless asked for, and silent either way.
 *
 * This exists because a "Page Unresponsive" dialog is almost impossible to
 * argue about without numbers. When it is enabled it can answer, for a running
 * page: which polling loops are alive, how many requests are outstanding and
 * what the high-water mark was, how often each render and sticky-geometry
 * recalculation ran, how often observer callbacks fired, and how much time the
 * main thread spent inside tasks long enough to stall a frame.
 *
 * Enable with `?diag=1` on any page, or `localStorage.setItem('omniDiag','1')`.
 * Read with `omniDiag.report()` in the console.
 *
 * When it is disabled every entry point is a no-op that returns immediately and
 * nothing is patched, so normal operation carries no cost and, importantly, no
 * extra logging. It never writes to the console on its own -- `report()` and
 * `log()` are the only output, and both are things a developer asks for.
 */
'use strict';

(function () {
  // Idempotent: this module patches fetch and setInterval when enabled, so a
  // second evaluation would wrap the already-wrapped versions and double
  // every count it reports.
  if (window.omniDiag) return;
  const enabled = (() => {
    try {
      if (new URLSearchParams(location.search).get('diag') === '1') return true;
      return localStorage.getItem('omniDiag') === '1';
    } catch (err) {
      return false;                       // private mode, or storage blocked
    }
  })();

  if (!enabled) {
    // The shape callers use, doing nothing. Instrumentation call sites are
    // written against this, so leaving them in costs a function call that
    // returns immediately.
    window.omniDiag = {
      enabled: false,
      count() {}, loop() {}, endLoop() {},
      report: () => ({enabled: false}),
      log() {},
    };
    return;
  }

  const started = performance.now();
  const counters = Object.create(null);
  const loops = Object.create(null);
  const longTasks = [];
  const requests = {total: 0, inflight: 0, max: 0, byUrl: Object.create(null), failed: 0};

  function count(name, by) {
    counters[name] = (counters[name] || 0) + (by === undefined ? 1 : by);
  }

  // --- polling loops, by name ---------------------------------------------
  // A loop registers itself so `report()` can say what is actually alive,
  // rather than leaving us to infer it from timer ids.
  function loop(name, periodMs) {
    loops[name] = {period: periodMs, started: Math.round(performance.now() - started), runs: 0,
                   overlapsSkipped: 0, alive: true};
  }
  function endLoop(name) {
    if (loops[name]) loops[name].alive = false;
  }

  // --- requests ------------------------------------------------------------
  const realFetch = window.fetch;
  window.fetch = function (...args) {
    const url = String(args[0] && args[0].url ? args[0].url : args[0])
      .replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    requests.total += 1;
    requests.byUrl[url] = (requests.byUrl[url] || 0) + 1;
    requests.inflight += 1;
    if (requests.inflight > requests.max) requests.max = requests.inflight;
    return realFetch.apply(this, args)
      .catch((err) => { requests.failed += 1; throw err; })
      .finally(() => { requests.inflight -= 1; });
  };

  // --- long tasks ----------------------------------------------------------
  // A task over 50ms is a frame the browser could not paint. Chrome shows its
  // "Page Unresponsive" dialog when the thread is blocked for several seconds,
  // so the distribution here is what tells us whether we are anywhere near it.
  try {
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) longTasks.push(Math.round(entry.duration));
    }).observe({entryTypes: ['longtask']});
  } catch (err) {
    counters['longtask.unavailable'] = 1;
  }

  // --- observer callback rate ---------------------------------------------
  // Counted by wrapping the constructors, so a runaway observer shows up as a
  // callback count far larger than the number of things that actually changed.
  ['MutationObserver', 'ResizeObserver'].forEach((name) => {
    const Real = window[name];
    if (typeof Real !== 'function') return;
    function Counted(callback) {
      return new Real(function (...args) {
        count(`observer.${name}`);
        return callback.apply(this, args);
      });
    }
    Counted.prototype = Real.prototype;
    window[name] = Counted;
  });

  // --- timers --------------------------------------------------------------
  const realSetInterval = window.setInterval;
  const liveIntervals = new Map();
  window.setInterval = function (fn, ms, ...rest) {
    const id = realSetInterval.call(this, fn, ms, ...rest);
    liveIntervals.set(id, ms);
    return id;
  };
  const realClearInterval = window.clearInterval;
  window.clearInterval = function (id) {
    liveIntervals.delete(id);
    return realClearInterval.call(this, id);
  };

  function report() {
    const elapsed = (performance.now() - started) / 1000;
    const over = (n) => longTasks.filter(t => t > n).length;
    return {
      enabled: true,
      seconds: Math.round(elapsed),
      loops,
      requests: {
        total: requests.total, failed: requests.failed,
        outstanding: requests.inflight, maxConcurrent: requests.max,
        perMinute: Math.round((requests.total / elapsed) * 60),
        byUrl: requests.byUrl,
      },
      longTasks: {
        count: longTasks.length,
        over100ms: over(100), over250ms: over(250), over1000ms: over(1000),
        longestMs: longTasks.length ? Math.max(...longTasks) : 0,
        totalBlockedMs: longTasks.reduce((a, b) => a + b, 0),
        blockedPercent: Math.round((longTasks.reduce((a, b) => a + b, 0) / (elapsed * 1000)) * 100),
      },
      counters,
      timers: {live: liveIntervals.size, periods: [...new Set(liveIntervals.values())].sort((a, b) => a - b)},
      dom: {nodes: document.getElementsByTagName('*').length},
      heapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null,
    };
  }

  window.omniDiag = {enabled: true, count, loop, endLoop, report,
                     log: () => console.log(JSON.stringify(report(), null, 2))};
})();
