"""Realistic tool-output generators for tests.

Each generator is deterministic (seeded), so tests always see the same text.
Each returns (log_text, must_keep), where must_keep lists lines that any
correct compression has to preserve: the errors and summaries a developer
would need to fix the problem.
"""

import random
from typing import List, Tuple

Log = Tuple[str, List[str]]


def pytest_failure(passes: int = 412) -> Log:
    lines = [
        "$ pytest -v",
        "============================= test session starts ==============================",
        "platform darwin -- Python 3.9.6, pytest-7.4.0",
        f"collected {passes + 1} items",
        "",
    ]
    for i in range(1, passes + 1):
        lines.append(f"tests/test_api.py::test_endpoint_{i} PASSED          [{i * 100 // passes:3d}%]")
        if i == passes // 2:
            lines.append("tests/test_api.py::test_refund_flow FAILED")
    must_keep = [
        "tests/test_api.py::test_refund_flow FAILED",
        "E   ValueError: refund exceeds captured amount",
        '  File "app/payments.py", line 52, in refund',
        "======================== 1 failed, 412 passed in 8.31s =========================",
    ]
    lines += [
        "",
        "=================================== FAILURES ===================================",
        "_______________________________ test_refund_flow _______________________________",
        "    def test_refund_flow(client):",
        ">       resp = client.post('/refund', json={'id': order.id})",
        must_keep[2],
        "    raise ValueError('refund exceeds captured amount')",
        must_keep[1],
        "=========================== short test summary info ============================",
        "FAILED tests/test_api.py::test_refund_flow - ValueError: refund exceeds captured amount",
        must_keep[3],
    ]
    return "\n".join(lines), must_keep


def npm_install(packages: int = 900) -> Log:
    rng = random.Random(1)
    lines = [
        f"\x1b[32mnpm\x1b[0m http fetch GET 200 https://registry.npmjs.org/pkg-{i} {rng.randint(5, 90)}ms"
        for i in range(packages)
    ]
    must_keep = [
        "npm WARN deprecated request@2.88.2: request has been deprecated",
        f"added {packages} packages in 12s",
    ]
    lines += [must_keep[0], "\r[#####     ] 50%\r[##########] 100%", must_keep[1]]
    return "\n".join(lines), must_keep


def cargo_error(units: int = 2000, seed: int = 7) -> Log:
    """A build log where every line differs, so dedupe can't help."""
    rng = random.Random(seed)
    words = ("alpha beta gamma delta parser lexer codegen runtime http tls json "
             "yaml serde tokio async core util net fs io").split()
    must_keep = [
        "error[E0308]: mismatched types",
        "  --> crates/net/src/client.rs:142:17",
        "error: could not compile `net` (lib) due to 1 previous error",
    ]
    lines = ["$ cargo build --release"]
    for i in range(units):
        a, b = rng.sample(words, 2)
        lines.append(
            f"   Compiling {a}-{b} v{rng.randint(0, 9)}.{rng.randint(0, 30)}.{rng.randint(0, 9)} (/src/{b}/{a})"
        )
        if i == units * 3 // 5:
            lines += [
                "warning: unused variable: `retries`",
                must_keep[0],
                must_keep[1],
                "142|     let timeout: u64 = config.timeout_ms;",
            ]
    lines += [must_keep[2], "warning: build failed, waiting for other jobs to finish..."]
    return "\n".join(lines), must_keep


def python_traceback(noise: int = 1500) -> Log:
    """A server log: lots of unique request lines, one crash in the middle."""
    rng = random.Random(3)
    paths = ["/api/users", "/api/orders", "/health", "/api/cart", "/login", "/static/app.js"]
    lines = []
    for i in range(noise):
        lines.append(
            f"2026-11-06 14:{i // 60 % 60:02d}:{i % 60:02d} INFO 127.0.0.1 "
            f"GET {rng.choice(paths)}?page={rng.randint(1, 500)} {rng.choice([200, 200, 304])} "
            f"{rng.randint(1, 900)}ms ua=Mozilla/5.0 req={rng.getrandbits(40):x}"
        )
        if i == noise // 3:
            lines += [
                "Traceback (most recent call last):",
                '  File "/srv/app/views.py", line 214, in checkout',
                "    total = cart.total() / cart.count",
                "ZeroDivisionError: division by zero",
            ]
    must_keep = ["Traceback (most recent call last):", "ZeroDivisionError: division by zero"]
    return "\n".join(lines), must_keep


ALL = {
    "pytest_failure": pytest_failure,
    "npm_install": npm_install,
    "cargo_error": cargo_error,
    "python_traceback": python_traceback,
}
