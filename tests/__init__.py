"""tests/ package marker.

The real network-forbidden guard lives in tests/fakes.py, not here: a
package's __init__.py is NOT guaranteed to run under
`python3 -m unittest discover -s tests` -- verified empirically, it does not
when top-level-dir defaults to the start dir. fakes.py is a plain module every
test file imports via sys.path, which always runs its top-level code exactly
once per process regardless of how the importing test file itself was
discovered/named. See tests/fakes.py and tests/test_network_guard.py.
"""
