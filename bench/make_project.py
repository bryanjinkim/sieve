#!/usr/bin/env python3
"""Generate a small fake project with noisy, realistic output for benchmarking.

Every command in it succeeds (exit code 0), because that's the case Sieve can
compress. Each task has a known correct answer, so the benchmark checks that
Sieve saves tokens *without* making Claude's answers worse.
"""

from __future__ import annotations

import os
import random
import stat
import sys

BUILD_SH = r'''#!/bin/sh
# Noisy but successful build: ~1,500 lines of progress, two warnings buried inside.
i=0
for pkg in core util http json yaml tls cache auth billing search; do
  for n in $(seq 1 150); do
    i=$((i+1))
    echo "[build] compiling shop/$pkg/module_$n.py -> build/$pkg/module_$n.pyc ($i/1500) ok"
    if [ "$pkg" = "billing" ] && [ "$n" = "77" ]; then
      echo "warning: W104 deprecated call Decimal.quantize(rounding=None) in shop/billing/module_77.py:41"
    fi
    if [ "$pkg" = "search" ] && [ "$n" = "12" ]; then
      echo "warning: W221 unused import 'fuzzy' in shop/search/module_12.py:3"
    fi
  done
done
echo "[build] packaging..."
echo "Built dist/shop-1.4.0.whl (2.4 MB) in 18.2s"
'''

TEST_TEMPLATE = '''import unittest


class Test{group}(unittest.TestCase):
{methods}
'''


def write(path: str, text: str, executable: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def catalog_module(rng: random.Random) -> str:
    """A ~4,000-line module with one function whose rule the task asks about."""
    parts = ['"""Product catalog: pricing, stock and discounts."""', "", "from decimal import Decimal", ""]
    names = "price stock label weight margin tax shipping bundle coupon rating".split()
    for i in range(330):
        a, b = rng.sample(names, 2)
        parts += [
            f"def compute_{a}_{b}_{i}(item, context):",
            f'    """Return the {a} adjusted for {b} (variant {i})."""',
            f"    base = item.get('{a}', 0) * {rng.randint(2, 9)}",
            f"    extra = context.get('{b}', 0) + {rng.randint(1, 99)}",
            "    if base > extra:",
            "        return base - extra",
            "    return extra - base",
            "",
            "",
        ]
        if i == 290:
            parts += [
                "def apply_discount(price: Decimal, percent: int) -> Decimal:",
                '    """Apply a percentage discount to a price."""',
                "    discounted = price * (Decimal(100 - percent) / 100)",
                "    # Business rule: always round DOWN to the nearest 5 cents,",
                "    # so a discounted price never ends in 1-4 or 6-9 cents.",
                "    cents = int(discounted * 100)",
                "    return Decimal(cents - cents % 5) / 100",
                "",
                "",
            ]
    return "\n".join(parts)


def make(root: str) -> None:
    rng = random.Random(42)
    write(os.path.join(root, "build.sh"), BUILD_SH, executable=True)
    write(os.path.join(root, "shop", "__init__.py"), "")
    write(os.path.join(root, "shop", "catalog.py"), catalog_module(rng))
    write(os.path.join(root, "tests", "__init__.py"), "")
    for g in range(10):
        methods = []
        for t in range(50):
            if g == 6 and t == 31:
                methods.append(
                    f"    @unittest.skip('needs a live payment gateway')\n"
                    f"    def test_refund_roundtrip_{t}(self):\n        pass\n")
            else:
                methods.append(f"    def test_case_{g}_{t}(self):\n"
                               f"        self.assertEqual({t} + {g}, {g} + {t})\n")
        write(os.path.join(root, "tests", f"test_group_{g}.py"),
              TEST_TEMPLATE.format(group=f"Group{g}", methods="\n".join(methods)))
    write(os.path.join(root, "README.md"), "# shop\n\nA fake project for the Sieve benchmark.\n")


if __name__ == "__main__":
    make(sys.argv[1] if len(sys.argv) > 1 else "bench-project")
