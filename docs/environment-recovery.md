# Dashboard environment configuration recovery

The September 19 Jetson setup failures involved SSH/rsync disconnections during
long native builds. A failed controller command did not reliably mean the remote
build had stopped. Worker 02 subsequently completed CUDA and pinned RPC builds;
dashboard action `180645_1750b4` verified all four Jetson workers READY.

Environment installation now retries only transient connectivity failures at the
failed stage, up to three attempts with 5s/10s backoff. Connection, bootstrap,
code synchronization, Python/CUDA setup and RPC preparation emit flushed stage
and attempt messages into the dashboard action log. Successful earlier stages
are not replayed within that request. The final readiness check remains mandatory.

Python installation waits up to two hours for its existing project lock. RPC
preparation now has its own project lock, waits for an existing build, and checks
the pinned runtime again after acquiring the lock. A completed runtime is reused;
incomplete RPC builds reuse their existing CMake directory. Remote command limits
allow four hours for lock waiting plus compilation. No broad process killing is
used, and no credentials or sudo authorization are added.

Permission errors, SSH identity failures, disk exhaustion and compiler errors
are not automatically retried. The failed stage and attempt count remain in the
final error. Worker API restart is not blindly retried. Recovery is bounded and
requires connectivity to return; it cannot repair hardware outages or missing
privileges. Dashboard process restart does not itself resume action threads;
requesting configuration again safely waits on the worker locks and reuses
verified installations.

Validation: recovery unit tests cover transient errors, timeouts, retry exhaustion,
hard-failure classification and successful stage reuse. Existing environment,
infrastructure, worker setup and security regression suites are also run. Real
hardware binaries are not rebuilt merely to inject failure scenarios.
