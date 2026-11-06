#!/usr/bin/env python3
"""
Builds the C++ algorithm core (netsim_algo.cpp) into a Python-importable
module. Works on Windows, macOS, and Linux — unlike build.sh, this doesn't
need a bash shell, since it's plain Python.

Usage (run from inside backend/algo/):
    python build.py

Requires:
    - a C++17 compiler (g++) available on your PATH
      Windows: install MSYS2/MinGW-w64, or use "x64 Native Tools Command
                Prompt for VS" if you have Visual Studio's C++ workload
      macOS:   xcode-select --install
      Linux:   sudo apt install build-essential
    - pybind11 installed (pip install pybind11)
"""

import os
import subprocess
import sys
import sysconfig

import pybind11


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    source = os.path.join(here, "netsim_algo.cpp")

    py_include = sysconfig.get_path("include")
    pybind_include = pybind11.get_include()
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or (
        ".pyd" if os.name == "nt" else ".so"
    )
    output = os.path.join(here, f"netsim_algo{ext_suffix}")

    cmd = [
        "g++", "-O3", "-Wall", "-shared", "-std=c++17",
        f"-I{py_include}", f"-I{pybind_include}",
        source, "-o", output,
    ]

    if os.name != "nt":
        cmd.insert(cmd.index("-shared") + 1, "-fPIC")
    else:
        # On Windows/MinGW, the compiled module needs to link against the
        # Python interpreter's import library (pythonXY.lib) so it can call
        # back into the Python C API at runtime.
        libdir = os.path.join(sys.base_prefix, "libs")
        pyver = f"python{sys.version_info.major}{sys.version_info.minor}"
        cmd += [f"-L{libdir}", f"-l{pyver}"]

        # Statically link the MinGW runtime (libstdc++, libgcc, libwinpthread)
        # directly into the .pyd. Without this, the module depends on those
        # as external DLLs — and since Python 3.8, Windows no longer searches
        # PATH for a compiled extension's dependent DLLs at import time, which
        # causes "DLL load failed" errors even when g++ itself works fine.
        cmd += ["-static-libgcc", "-static-libstdc++", "-static"]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print("\nBuild failed. Common causes:")
        print("  - g++ not installed or not on PATH (see requirements at top of this file)")
        print("  - On Windows: make sure you installed Python's headers (default with python.org installer)")
        sys.exit(1)

    print(f"\nBuild succeeded: {output}")
    print("You can now run: python ../server.py")


if __name__ == "__main__":
    main()