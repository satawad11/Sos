#!/usr/bin/env python3
"""Regenerate loc_vectors.inc from the backend codec (run from repo root): python3 firmware/test/gen_vectors.py > firmware/test/loc_vectors.inc"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from app import codec
print("// GENERATED from backend/app/codec.py encode_loc(): python3 firmware/test/gen_vectors.py > firmware/test/loc_vectors.inc")
for a, la, lo, s, h in [(5,13.9115,100.5954,8,1.2),(255,-33.8688,151.2093,15,9.9),(1,89.99,-179.99,4,0.0),(200,-89.99,179.99,1,1.25),(7,13.75,100.5,12,0.75),(9,0.0015,0.0015,6,3.5),(3,13.7563,100.5018,9,1.75)]:
    print(f'  {{{a}, {la!r}, {lo!r}, {s}, {h!r}, "{codec.encode_loc(a,la,lo,s,h).hex()}"}},')
