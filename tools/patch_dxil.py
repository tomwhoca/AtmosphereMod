"""Patch our medium model into stock shaders that evaluate the atmosphere inline.

    python tools/patch_dxil.py            # every target in shaders/manifest.json "patches"
    python tools/patch_dxil.py --keep-ir  # also keep the patched IR text in build/patched/ir

Every pass we change is the user's own stock shader, edited in place: the sky
passes (the transmittance, multiple-scattering and sky-view LUTs, the camera
volume, the far cloud layer's map) and the passes that run their own copy of
the atmosphere march (ps_deferredRendering's per-pixel aerial perspective on
terrain, ch_sky's cubemap):
  1. disassemble the stock DXIL to LLVM IR text (dxc -dumpbin);
  2. find the inline medium by its data flow (the SkyAtmosphere cbuffer loads
     feeding exp(), the HG phase from MiePhaseG) and splice in the same maths
     as our medium model: Mie density and colour (Angstrom),
     reference Rayleigh/ozone, brown-haze absorption, HG g and backscatter lobe.
     The cloud renderer instead gets per-colour haze (T11), a forward lobe in
     its sun/moon phase, tunable multiple scattering, the build-up of bounced
     light, crisper edges, a glory, the aircraft's shadow, optical thickness
     and surface detail (CloudParams C0-C31);
  3. add our ModParams cbuffer (b0 space50, the injected root constants) to the
     resource metadata and load the tunables at the entry point; for the
     clouds also CloudParams (b1 space50, compute only);
  4. assemble with tools/dxilasm (validated and signed by the SDK's dxil.dll).
Every match is asserted, so a sim update that changes the code fails loudly
instead of producing a wrong shader.

With T0 = 0 the patched shader computes exactly the stock values (selects and
zero-weighted lerps). Game targets go to build/shaders/<crc>.cso, lab-only
targets (compute shaders we also replicate in HLSL, used to test this patcher
in atmolab) to build/patched/<crc>.cso.
"""

import argparse
import csv
import json
import math
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from disasm import find_dxc  # noqa: E402

DXILASM = ROOT / "build" / "dxilasm" / "dxilasm.exe"

# Our medium model's constants (the developer's HLSL replicas use the same).
REFERENCE_RAYLEIGH = (5.802e-3, 13.558e-3, 33.1e-3)
REFERENCE_OZONE = (0.650e-3, 1.881e-3, 0.085e-3)
BROWN_HAZE_TINT = (0.15, 0.3, 0.6)
BACKSCATTER_G = -0.4
AEROSOL_SPECTRAL_LOG2 = (-0.30610, 0.0, 0.32193)
MOD_SPACE = 50
MOD_PARAMS_SIZE = 64
CLOUD_PARAMS_SIZE = 128  # C0..C31 at b1 space50, compute only

LOG2E = "0x3FF7154760000000"
NEG_LOG2E = "0xBFF7154760000000"

V = r"%[\w.]+"
ANNOTATE_CB = re.compile(rf"^\s+({V}) = call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, "
                         rf"%dx\.types\.Handle ({V}), %dx\.types\.ResourceProperties \{{ i32 13, i32 (\d+) \}}\)")
RESRET = re.compile(rf"^\s+({V}) = extractvalue %dx\.types\.ResRet\.f32 ({V}), (\d)")
PHI = re.compile(rf"^\s+({V}) = phi float ")
LOAD = re.compile(rf"^\s+({V}) = call %dx\.types\.CBufRet\.f32 @dx\.op\.cbufferLoadLegacy\.f32\(i32 59, "
                  rf"%dx\.types\.Handle ({V}), i32 (\d+)\)")
EXTRACT = re.compile(rf"^\s+({V}) = extractvalue %dx\.types\.CBufRet\.f32 ({V}), (\d)")
BINOP = re.compile(rf"^\s+({V}) = (fmul|fadd|fsub|fdiv) fast float (\S+), (\S+?)(\s+;.*)?$")
UNARY = re.compile(rf"^\s+({V}) = call float @dx\.op\.unary\.f32\(i32 (\d+), float ({V})\)")
TERTIARY = re.compile(rf"^\s+({V}) = call float @dx\.op\.tertiary\.f32\(i32 46, float (\S+), float (\S+), float (\S+)\)")
DOT3 = re.compile(rf"^\s+({V}) = call float @dx\.op\.dot3\.f32\(i32 55, float (\S+), float (\S+), float (\S+), float (\S+), float (\S+), float (\S+)\)")
DEFINE = re.compile(rf"^\s+({V}) = ")

OP_EXP, OP_SATURATE, OP_SQRT, OP_LOG2 = "21", "7", "24", "23"


def literal_value(tok: str) -> float:
    """An IR float literal (decimal or 0x double bits)."""
    if tok.startswith("0x"):
        return struct.unpack("<d", struct.pack("<Q", int(tok, 16)))[0]
    return float(tok)


def f32(x: float) -> str:
    """An LLVM float literal: the double hex of the nearest float."""
    return "0x%016X" % struct.unpack("<Q", struct.pack("<d", struct.unpack("<f", struct.pack("<f", x))[0]))[0]


class PatchError(Exception):
    pass


def need(cond, what: str):
    if not cond:
        raise PatchError(what)


class Ir:
    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.reindex()

    def reindex(self):
        self.defs = {}
        self.binops = {}
        self.unary = {}
        self.fmad = {}   # FMad(a, b, c) = a * b + c
        self.dot3 = {}
        self.extracts = {}
        self.loads = {}
        self.atmo_handles = set()
        self.cbs = {}     # annotated cbuffer handle -> (binding handle, size in bytes)
        self.resret = {}  # value -> (resource load/sample, component)
        for i, line in enumerate(self.lines):
            if m := DEFINE.match(line):
                self.defs[m[1]] = i
            if m := ANNOTATE_CB.match(line):
                self.cbs[m[1]] = (m[2], int(m[3]))
                if int(m[3]) == 208:
                    self.atmo_handles.add(m[1])
            elif m := RESRET.match(line):
                self.resret[m[1]] = (m[2], int(m[3]))
            elif m := LOAD.match(line):
                self.loads[m[1]] = (m[2], int(m[3]))
            elif m := EXTRACT.match(line):
                self.extracts[m[1]] = (m[2], int(m[3]))
            elif m := BINOP.match(line):
                self.binops[m[1]] = (m[2], m[3], m[4])
            elif m := UNARY.match(line):
                self.unary[m[1]] = (m[2], m[3])
            elif m := TERTIARY.match(line):
                self.fmad[m[1]] = (m[2], m[3], m[4])
            elif m := DOT3.match(line):
                self.dot3[m[1]] = tuple(m[i] for i in range(2, 8))

    def atmo(self, value: str):
        """(register, component) if value is a SkyAtmosphere constant."""
        if value in self.extracts:
            load, comp = self.extracts[value]
            if load in self.loads and self.loads[load][0] in self.atmo_handles:
                return self.loads[load][1], comp
        return None

    def users(self, value: str):
        pat = re.compile(re.escape(value) + r"\b")
        return [i for i, line in enumerate(self.lines)
                if pat.search(line) and self.defs.get(value) != i and not line.lstrip().startswith(";")]

    def def_name(self, line_index: int) -> str:
        return DEFINE.match(self.lines[line_index])[1]


def find_operand(ir: Ir, value: str, pred, depth: int = 0, limit: int = 8):
    """Depth-first search of the defining binops/unaries of value for pred."""
    if depth > limit:
        return None
    if pred(value):
        return value
    operands = []
    if value in ir.binops:
        operands = ir.binops[value][1:]
    elif value in ir.unary:
        operands = [ir.unary[value][1]]
    for op in operands:
        if op.startswith("%") and (found := find_operand(ir, op, pred, depth + 1, limit)):
            return found
    return None


def prologue_lines() -> list[str]:
    """Load the tunables (ModParams, b0 space50) and derive the model's constants."""
    t = "%dx.types.CBufRet.f32"
    prologue = [
        f"  %mod.h = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
        f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 2 }}, i32 0, i1 false)",
        f"  %mod.ah = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.h, "
        f"%dx.types.ResourceProperties {{ i32 13, i32 {MOD_PARAMS_SIZE} }})",
    ]
    for r in range(3):
        prologue.append(f"  %mod.r{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.ah, i32 {r + 1})")
    for n in range(12):
        prologue.append(f"  %mod.T{n} = extractvalue {t} %mod.r{n // 4}, {n % 4}")
    milli = f32(0.001)
    prologue += [
        "  %mod.mix = call float @dx.op.unary.f32(i32 7, float %mod.T0)",
        "  %mod.off = fcmp fast ole float %mod.mix, 0.000000e+00",
        f"  %mod.edge = call float @dx.op.binary.f32(i32 35, float %mod.T2, float {milli})",
        f"  %mod.blk = fdiv fast float {LOG2E}, %mod.edge",
        f"  %mod.fts = call float @dx.op.binary.f32(i32 35, float %mod.T5, float {milli})",
        f"  %mod.ftk = fdiv fast float {NEG_LOG2E}, %mod.fts",
        "  %mod.ref0 = call float @dx.op.unary.f32(i32 7, float %mod.T8)",
        "  %mod.ref = fmul fast float %mod.mix, %mod.ref0",
        "  %mod.w0 = call float @dx.op.unary.f32(i32 7, float %mod.T7)",
        "  %mod.w = fmul fast float %mod.mix, %mod.w0",
        "  %mod.brown = fmul fast float %mod.mix, %mod.T6",
        "  %mod.ied = fdiv fast float 1.000000e+00, %mod.edge",
        "  %mod.gt0 = call float @dx.op.binary.f32(i32 35, float %mod.T9, float 0.000000e+00)",
        f"  %mod.gt = call float @dx.op.binary.f32(i32 36, float %mod.gt0, float {f32(0.95)})",
        "  %mod.gon = fcmp fast ogt float %mod.gt, 0.000000e+00",
        "  %mod.ae0 = call float @dx.op.binary.f32(i32 35, float %mod.T10, float 0.000000e+00)",
        "  %mod.ae = call float @dx.op.binary.f32(i32 36, float %mod.ae0, float 3.000000e+00)",
    ]
    for c in range(3):
        prologue += [
            f"  %mod.spx{c} = fmul fast float %mod.ae, {f32(AEROSOL_SPECTRAL_LOG2[c])}",
            f"  %mod.spe{c} = call float @dx.op.unary.f32(i32 21, float %mod.spx{c})",
            f"  %mod.spd{c} = fsub fast float %mod.spe{c}, 1.000000e+00",
            f"  %mod.spec{c} = call float @dx.op.tertiary.f32(i32 46, float %mod.spd{c}, float %mod.mix, "
            f"float 1.000000e+00)",
        ]
    return prologue


def cloud_prologue_lines() -> list[str]:
    """Load the cloud tunables (CloudParams, b1 space50, compute only) and derive
    the forward lobe's constants (weight = C0 * C2 in 0..0.5, g = C1 in 0..0.99),
    the multiple-scattering ones (C3-C5, apply_cloud_ms) and the shape, glory
    and aircraft-shadow ones (C6-C15, apply_cloud_shape)."""
    t = "%dx.types.CBufRet.f32"
    lines = [
        f"  %mod.ch = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
        f"{{ i32 1, i32 1, i32 {MOD_SPACE}, i8 2 }}, i32 1, i1 false)",
        f"  %mod.cah = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.ch, "
        f"%dx.types.ResourceProperties {{ i32 13, i32 {CLOUD_PARAMS_SIZE} }})",
    ]
    for r in range(CLOUD_PARAMS_SIZE // 16):
        lines.append(f"  %mod.cr{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.cah, i32 {r})")
    for n in range(CLOUD_PARAMS_SIZE // 4):
        lines.append(f"  %mod.C{n} = extractvalue {t} %mod.cr{n // 4}, {n % 4}")
    lines += [
        "  %mod.cmix = call float @dx.op.unary.f32(i32 7, float %mod.C0)",
        "  %mod.cw0 = call float @dx.op.binary.f32(i32 35, float %mod.C2, float 0.000000e+00)",
        "  %mod.cw1 = call float @dx.op.binary.f32(i32 36, float %mod.cw0, float 5.000000e-01)",
        "  %mod.cw = fmul fast float %mod.cmix, %mod.cw1",
        "  %mod.coff = fcmp fast ole float %mod.cw, 0.000000e+00",
        "  %mod.cg0 = call float @dx.op.binary.f32(i32 35, float %mod.C1, float 0.000000e+00)",
        f"  %mod.cg = call float @dx.op.binary.f32(i32 36, float %mod.cg0, float {f32(0.99)})",
        "  %mod.cg2 = fmul fast float %mod.cg, %mod.cg",
        "  %mod.cgn0 = fsub fast float 1.000000e+00, %mod.cg2",
        f"  %mod.cgn = fmul fast float %mod.cgn0, {f32(1.0 / (4.0 * 3.14159265358979))}",
        "  %mod.cgd = fadd fast float %mod.cg2, 1.000000e+00",
        "  %mod.cgk = fmul fast float %mod.cg, -2.000000e+00",
        # multiple scattering (apply_cloud_ms): edge share C0 * C3, lobe g C0 * C4 (0..0.9)
        "  %mod.cms0 = call float @dx.op.unary.f32(i32 7, float %mod.C3)",
        "  %mod.cms = fmul fast float %mod.cmix, %mod.cms0",
        "  %mod.cmsoff = fcmp fast ole float %mod.cms, 0.000000e+00",
        "  %mod.cmsk = fsub fast float 1.000000e+00, %mod.cms",
        "  %mod.cmg0 = call float @dx.op.binary.f32(i32 35, float %mod.C4, float 0.000000e+00)",
        f"  %mod.cmg1 = call float @dx.op.binary.f32(i32 36, float %mod.cmg0, float {f32(0.9)})",
        "  %mod.cmg = fmul fast float %mod.cmix, %mod.cmg1",
        "  %mod.cmgoff = fcmp fast ole float %mod.cmg, 0.000000e+00",
        "  %mod.cmg2 = fmul fast float %mod.cmg, %mod.cmg",
        "  %mod.cmgn0 = fsub fast float 1.000000e+00, %mod.cmg2",
        f"  %mod.cmgn = fmul fast float %mod.cmgn0, {f32(1.0 / (4.0 * 3.14159265358979))}",
        "  %mod.cmgd = fadd fast float %mod.cmg2, 1.000000e+00",
        "  %mod.cmgk = fmul fast float %mod.cmg, -2.000000e+00",
        # deep-interior sunlight C5 (0.001..0.9): T = exp(0.6 - d / (1 + d / A)), exp(0.6 - A) = C5
        f"  %mod.cf0 = call float @dx.op.binary.f32(i32 35, float %mod.C5, float {f32(0.001)})",
        f"  %mod.cf1 = call float @dx.op.binary.f32(i32 36, float %mod.cf0, float {f32(0.9)})",
        "  %mod.cfl = call float @dx.op.unary.f32(i32 23, float %mod.cf1)",
        f"  %mod.cfa = call float @dx.op.tertiary.f32(i32 46, float %mod.cfl, float {f32(-0.6931471805599453)}, "
        f"float {f32(0.6)})",
        "  %mod.cfr = fdiv fast float 1.000000e+00, %mod.cfa",
        "  %mod.cfon0 = fcmp fast ogt float %mod.C5, 0.000000e+00",
        "  %mod.cfon1 = fcmp fast ogt float %mod.cmix, 0.000000e+00",
        "  %mod.cfon = and i1 %mod.cfon0, %mod.cfon1",
        "  %mod.cfk = select i1 %mod.cfon, float %mod.cfr, float 2.500000e-01",
        # build-up of bounced light (apply_cloud_shape): strength C0 * C6, depth C7 (m, 100 if <= 0)
        "  %mod.cpw0 = call float @dx.op.unary.f32(i32 7, float %mod.C6)",
        "  %mod.cpw = fmul fast float %mod.cmix, %mod.cpw0",
        "  %mod.cpwoff = fcmp fast ole float %mod.cpw, 0.000000e+00",
        "  %mod.cpd0 = call float @dx.op.binary.f32(i32 35, float %mod.C7, float 1.000000e+00)",
        "  %mod.cpdz = fcmp fast ole float %mod.C7, 0.000000e+00",
        "  %mod.cpd = select i1 %mod.cpdz, float 1.000000e+02, float %mod.cpd0",
        f"  %mod.cpk = fdiv fast float {NEG_LOG2E}, %mod.cpd",
        # crisper edges: ramp slope 1 + C0 * C8 (C8 in 0..8)
        "  %mod.ce0 = call float @dx.op.binary.f32(i32 35, float %mod.C8, float 0.000000e+00)",
        "  %mod.ce1 = call float @dx.op.binary.f32(i32 36, float %mod.ce0, float 8.000000e+00)",
        "  %mod.ce = fmul fast float %mod.cmix, %mod.ce1",
        "  %mod.ceoff = fcmp fast ole float %mod.ce, 0.000000e+00",
        "  %mod.cek = fadd fast float %mod.ce, 1.000000e+00",
        # glory: strength 0.1 * C0 * C9, droplet radius C10 (um, 1..50, 10 if <= 0),
        # size spread C11 (0.01..0.5, 0.15 if <= 0)
        "  %mod.cgl0 = call float @dx.op.binary.f32(i32 35, float %mod.C9, float 0.000000e+00)",
        "  %mod.cgl1 = fmul fast float %mod.cmix, %mod.cgl0",
        "  %mod.cgloff = fcmp fast ole float %mod.cgl1, 0.000000e+00",
        f"  %mod.cgl = fmul fast float %mod.cgl1, {f32(0.1)}",
        "  %mod.cgr0 = call float @dx.op.binary.f32(i32 35, float %mod.C10, float 1.000000e+00)",
        "  %mod.cgr1 = call float @dx.op.binary.f32(i32 36, float %mod.cgr0, float 5.000000e+01)",
        "  %mod.cgrz = fcmp fast ole float %mod.C10, 0.000000e+00",
        "  %mod.cgr = select i1 %mod.cgrz, float 1.000000e+01, float %mod.cgr1",
        *[f"  %mod.cgx{c} = fmul fast float %mod.cgr, {f32(2 * math.pi / GLORY_WAVELENGTHS[c])}" for c in range(3)],
        f"  %mod.cgs0 = call float @dx.op.binary.f32(i32 35, float %mod.C11, float {f32(0.01)})",
        "  %mod.cgs1 = call float @dx.op.binary.f32(i32 36, float %mod.cgs0, float 5.000000e-01)",
        "  %mod.cgsz = fcmp fast ole float %mod.C11, 0.000000e+00",
        f"  %mod.cgs = select i1 %mod.cgsz, float {f32(0.15)}, float %mod.cgs1",
        # aircraft shadow: darkness C0 * C12, relative heading C13 (heading - sun azimuth, rad),
        # bank C14 (rad, right wing down positive), span C15 (m; 0 = no aircraft data -> off);
        # C13-C15 come from the add-on (SimConnect), not tunables.ini
        "  %mod.cshs0 = call float @dx.op.unary.f32(i32 7, float %mod.C12)",
        "  %mod.cshs = fmul fast float %mod.cmix, %mod.cshs0",
        "  %mod.cshspan = call float @dx.op.binary.f32(i32 35, float %mod.C15, float 0.000000e+00)",
        f"  %mod.cshlf = fmul fast float %mod.cshspan, {f32(SHADOW_LENGTH_PER_SPAN)}",
        "  %mod.cshoff0 = fcmp fast ole float %mod.cshs, 0.000000e+00",
        "  %mod.cshoff1 = fcmp fast ole float %mod.cshspan, 0.000000e+00",
        "  %mod.cshoff = or i1 %mod.cshoff0, %mod.cshoff1",
        "  %mod.cshcd = call float @dx.op.unary.f32(i32 12, float %mod.C13)",
        "  %mod.cshsd = call float @dx.op.unary.f32(i32 13, float %mod.C13)",
        "  %mod.cshcb = call float @dx.op.unary.f32(i32 12, float %mod.C14)",
        "  %mod.cshsb = call float @dx.op.unary.f32(i32 13, float %mod.C14)",
        # optical thickness (apply_cloud_optical): C16 at a cumulus core (0..20, off if <= 0), profile
        # exponent C17 (0.2..1, 1 if <= 0), ice altitude C18 (m, 6000 if <= 0), ice factor C19 (0..20, 1 if <= 0)
        "  %mod.cok0 = call float @dx.op.binary.f32(i32 35, float %mod.C16, float 0.000000e+00)",
        "  %mod.cok1 = call float @dx.op.binary.f32(i32 36, float %mod.cok0, float 2.000000e+01)",
        f"  %mod.cok = fmul fast float %mod.cok1, {f32(OPTICAL_REF)}",
        "  %mod.cooff0 = fcmp fast ole float %mod.C16, 0.000000e+00",
        "  %mod.cooff1 = fcmp fast ole float %mod.cmix, 0.000000e+00",
        "  %mod.cooff = or i1 %mod.cooff0, %mod.cooff1",
        f"  %mod.cog0 = call float @dx.op.binary.f32(i32 35, float %mod.C17, float {f32(0.2)})",
        "  %mod.cog1 = call float @dx.op.binary.f32(i32 36, float %mod.cog0, float 1.000000e+00)",
        "  %mod.cogz = fcmp fast ole float %mod.C17, 0.000000e+00",
        "  %mod.cog = select i1 %mod.cogz, float 1.000000e+00, float %mod.cog1",
        "  %mod.cohz = fcmp fast ole float %mod.C18, 0.000000e+00",
        f"  %mod.coh = select i1 %mod.cohz, float {f32(OPTICAL_ICE_DEFAULT_M)}, float %mod.C18",
        "  %mod.coki0 = call float @dx.op.binary.f32(i32 35, float %mod.C19, float 0.000000e+00)",
        "  %mod.coki1 = call float @dx.op.binary.f32(i32 36, float %mod.coki0, float 2.000000e+01)",
        "  %mod.cokiz = fcmp fast ole float %mod.C19, 0.000000e+00",
        "  %mod.coki = select i1 %mod.cokiz, float 1.000000e+00, float %mod.coki1",
        # surface detail (apply_cloud_detail): base smoothing C0 * C28 (0..1) over C29 m (300 if <= 0),
        # a finer octave C0 * C30 (0..0.1) at a C31 m period (50..1000, 350 if <= 0)
        "  %mod.cbs0 = call float @dx.op.unary.f32(i32 7, float %mod.C28)",
        "  %mod.cbs = fmul fast float %mod.cmix, %mod.cbs0",
        "  %mod.cbsoff = fcmp fast ole float %mod.cbs, 0.000000e+00",
        "  %mod.cbd0 = call float @dx.op.binary.f32(i32 35, float %mod.C29, float 1.000000e+01)",
        "  %mod.cbdz = fcmp fast ole float %mod.C29, 0.000000e+00",
        f"  %mod.cbd = select i1 %mod.cbdz, float {f32(DETAIL_BASE_DEPTH_M)}, float %mod.cbd0",
        "  %mod.cbdi = fdiv fast float 1.000000e+00, %mod.cbd",
        "  %mod.cfo0 = call float @dx.op.binary.f32(i32 35, float %mod.C30, float 0.000000e+00)",
        f"  %mod.cfo1 = call float @dx.op.binary.f32(i32 36, float %mod.cfo0, float {f32(0.1)})",
        "  %mod.cfo = fmul fast float %mod.cmix, %mod.cfo1",
        "  %mod.cfooff = fcmp fast ole float %mod.cfo, 0.000000e+00",
        "  %mod.cfp0 = call float @dx.op.binary.f32(i32 35, float %mod.C31, float 5.000000e+01)",
        "  %mod.cfp1 = call float @dx.op.binary.f32(i32 36, float %mod.cfp0, float 1.000000e+03)",
        "  %mod.cfpz = fcmp fast ole float %mod.C31, 0.000000e+00",
        f"  %mod.cfp = select i1 %mod.cfpz, float {f32(DETAIL_FINE_PERIOD_M)}, float %mod.cfp1",
        "  %mod.cff = fdiv fast float 1.000000e+03, %mod.cfp",
        "  %mod.cfol = call float @dx.op.unary.f32(i32 23, float %mod.cff)",
    ]
    return lines


def finish(ir: Ir, cloud: bool = False, debug_uav: bool = False) -> str:
    """Insert the tunables prologue, the declarations we use and our cbuffer
    metadata; with cloud, also the cloud tunables (b1 space50); with
    debug_uav, the debug buffer (RWByteAddressBuffer u0 space50)."""
    entry = next(i for i, l in enumerate(ir.lines) if l.startswith("define void @"))
    ir.lines[entry + 1:entry + 1] = prologue_lines() + (cloud_prologue_lines() if cloud else [])

    # ---- declarations we use
    text = "\n".join(ir.lines)
    attrs = {}
    for m in re.finditer(r"^declare \S+ @(dx\.op\.[\w.]+)\(.*\) (#\d+)$", text, re.M):
        attrs[m[1]] = m[2]
    need("dx.op.unary.f32" in attrs and "dx.op.cbufferLoadLegacy.f32" in attrs
         and "dx.op.createHandleFromBinding" in attrs and "dx.op.annotateHandle" in attrs,
         "missing a dx.op declaration we rely on")
    extra = []
    if "dx.op.binary.f32" not in attrs:
        extra.append(f"declare float @dx.op.binary.f32(i32, float, float) {attrs['dx.op.unary.f32']}")
    if "dx.op.tertiary.f32" not in attrs:
        extra.append(f"declare float @dx.op.tertiary.f32(i32, float, float, float) {attrs['dx.op.unary.f32']}")
    if debug_uav:
        store_attr = next((a for op, a in attrs.items() if "Store" in op), None)
        need(store_attr, "no store declaration to borrow attributes from")
        for kind, ty in (("i32", "i32"), ("f32", "float")):
            # (the validator rejects unused declarations)
            if f"dx.op.rawBufferStore.{kind}" not in attrs and f"@dx.op.rawBufferStore.{kind}(" in text:
                extra.append(f"declare void @dx.op.rawBufferStore.{kind}(i32, %dx.types.Handle, i32, i32, {ty}, {ty}, "
                             f"{ty}, {ty}, i8, i32) {store_attr}")
    ray_query = "@dx.op.rayQuery_TraceRayInline(" in text
    if ray_query:
        nounwind = next((a for op, a in attrs.items() if "Store" in op), None)
        need(nounwind, "no store declaration to borrow attributes from")
        readonly = attrs["dx.op.cbufferLoadLegacy.f32"]
        for op, decl, attr in (
                ("allocateRayQuery", "declare i32 @dx.op.allocateRayQuery(i32, i32)", nounwind),
                ("rayQuery_TraceRayInline", "declare void @dx.op.rayQuery_TraceRayInline(i32, i32, %dx.types.Handle, "
                 "i32, i32, float, float, float, float, float, float, float, float)", nounwind),
                ("rayQuery_Proceed.i1", "declare i1 @dx.op.rayQuery_Proceed.i1(i32, i32)", nounwind),
                ("rayQuery_StateScalar.i32", "declare i32 @dx.op.rayQuery_StateScalar.i32(i32, i32)", readonly)):
            if f"dx.op.{op}" not in attrs:
                extra.append(f"{decl} {attr}")
    readonly = attrs["dx.op.cbufferLoadLegacy.f32"]
    if "@dx.op.cbufferLoadLegacy.i32(" in text and "dx.op.cbufferLoadLegacy.i32" not in attrs:
        extra.append("declare %dx.types.CBufRet.i32 @dx.op.cbufferLoadLegacy.i32(i32, %dx.types.Handle, i32) "
                     f"{readonly}")
    if "@dx.op.textureLoad.f32(" in text and "dx.op.textureLoad.f32" not in attrs:
        extra.append("declare %dx.types.ResRet.f32 @dx.op.textureLoad.f32(i32, %dx.types.Handle, i32, i32, i32, i32, "
                     f"i32, i32, i32) {readonly}")
    if "@dx.op.getDimensions(" in text and "dx.op.getDimensions" not in attrs:
        extra.append(f"declare %dx.types.Dimensions @dx.op.getDimensions(i32, %dx.types.Handle, i32) {readonly}")
    if "@dx.op.rawBufferLoad.f32(" in text and "dx.op.rawBufferLoad.f32" not in attrs:
        need("%dx.types.ResRet.f32 = type" in text, "no ResRet.f32 type for rawBufferLoad")
        extra.append("declare %dx.types.ResRet.f32 @dx.op.rawBufferLoad.f32(i32, %dx.types.Handle, i32, i32, i8, i32) "
                     f"{attrs['dx.op.cbufferLoadLegacy.f32']}")
    if extra:
        last_decl = max(m.end() for m in re.finditer(r"^declare .*$", text, re.M))
        text = text[:last_decl] + "\n" + "\n".join(extra) + text[last_decl:]

    # ---- our cbuffers in the resource metadata
    ours = [("%CBufferAtmosphereMod", 0, MOD_PARAMS_SIZE)]
    types = ("\n%struct.AtmosphereModParams = type { i32, i32, <2 x i32>, [3 x <4 x float>] }"
             "\n%CBufferAtmosphereMod = type { %struct.AtmosphereModParams }")
    if cloud:
        ours.append(("%CBufferAtmosphereModCloud", 1, CLOUD_PARAMS_SIZE))
        types += ("\n%struct.AtmosphereModCloud = type { [8 x <4 x float>] }"
                  "\n%CBufferAtmosphereModCloud = type { %struct.AtmosphereModCloud }")
    if debug_uav and "%struct.RWByteAddressBuffer = type" not in text:
        types += "\n%struct.RWByteAddressBuffer = type { i32 }"
    if "@dx.op.getDimensions(" in text and "%dx.types.Dimensions = type" not in text:
        types += "\n%dx.types.Dimensions = type { i32, i32, i32, i32 }"
    if "@dx.op.cbufferLoadLegacy.i32(" in text and "%dx.types.CBufRet.i32 = type" not in text:
        types += "\n%dx.types.CBufRet.i32 = type { i32, i32, i32, i32 }"
    last_type = max(m.end() for m in re.finditer(r"^%[^\n]+ = type [^\n]+$", text, re.M))
    text = text[:last_type] + types + text[last_type:]
    res = re.search(r"^!dx\.resources = !\{!(\d+)\}$", text, re.M)
    need(res, "no !dx.resources")
    lists = re.search(rf"^!{res[1]} = !\{{(.*)\}}$", text, re.M)
    srv, uav, cbv, smp = [x.strip() for x in lists[1].split(",")]
    next_md = max(int(n) for n in re.findall(r"^!(\d+) = ", text, re.M)) + 1
    if cbv == "null":
        cb_ids, cbv_line = [], None
    else:
        cbv_line = re.search(rf"^{re.escape(cbv)} = !\{{(.*)\}}$", text, re.M)
        cb_ids = [int(re.search(rf"^{re.escape(e.strip())} = !\{{i32 (\d+),", text, re.M)[1])
                  for e in cbv_line[1].split(",")]
    first_id = max(cb_ids, default=-1) + 1
    entries = [f"!{next_md + k} = !{{i32 {first_id + k}, {name}* undef, !\"\", "
               f"i32 {MOD_SPACE}, i32 {register}, i32 1, i32 {size}, null}}"
               for k, (name, register, size) in enumerate(ours)]
    refs = ", ".join(f"!{next_md + k}" for k in range(len(ours)))
    if cbv_line is None:
        new_list = f"!{next_md + len(ours)} = !{{{refs}}}"
        text = text.replace(lists[0], f"!{res[1]} = !{{{srv}, {uav}, !{next_md + len(ours)}, {smp}}}")
        text += "\n" + "\n".join(entries) + "\n" + new_list + "\n"
    else:
        text = text.replace(cbv_line[0], f"{cbv} = !{{{cbv_line[1]}, {refs}}}")
        text += "\n" + "\n".join(entries) + "\n"

    if debug_uav:
        res = re.search(r"^!dx\.resources = !\{!(\d+)\}$", text, re.M)
        lists = re.search(rf"^!{res[1]} = !\{{(.*)\}}$", text, re.M)
        srv, uav, cbv, smp = [x.strip() for x in lists[1].split(",")]
        md = max(int(n) for n in re.findall(r"^!(\d+) = ", text, re.M)) + 1
        if uav == "null":
            uav_ids, uav_line = [], None
        else:
            uav_line = re.search(rf"^{re.escape(uav)} = !\{{(.*)\}}$", text, re.M)
            uav_ids = [int(re.search(rf"^{re.escape(e.strip())} = !\{{i32 (\d+),", text, re.M)[1])
                       for e in uav_line[1].split(",")]
        entry = (f"!{md} = !{{i32 {max(uav_ids, default=-1) + 1}, %struct.RWByteAddressBuffer* undef, !\"\", "
                 f"i32 {MOD_SPACE}, i32 0, i32 1, i32 11, i1 false, i1 false, i1 false, null}}")
        if uav_line is None:
            text = text.replace(lists[0], f"!{res[1]} = !{{{srv}, !{md + 1}, {cbv}, {smp}}}")
            text += entry + "\n" + f"!{md + 1} = !{{!{md}}}\n"
        else:
            text = text.replace(uav_line[0], f"{uav} = !{{{uav_line[1]}, !{md}}}")
            text += entry + "\n"
        # the entry point's shader flags (tag 0) must declare raw buffer use
        flags = list(re.finditer(r"!\{i32 0, i64 (\d+)", text))
        need(len(flags) == 1, f"{len(flags)} shader-flag entries")
        f = flags[0]
        text = text[:f.start(1)] + str(int(f[1]) | 0x10) + text[f.end(1):]

    if ray_query:
        # MSFS's TLAS as t0 space50 (RaytracingAccelerationStructure, resource kind 16)
        if "%struct.RaytracingAccelerationStructure = type" not in text:
            last_type = max(m.end() for m in re.finditer(r"^%[^\n]+ = type [^\n]+$", text, re.M))
            text = text[:last_type] + "\n%struct.RaytracingAccelerationStructure = type { i32 }" + text[last_type:]
        res = re.search(r"^!dx\.resources = !\{!(\d+)\}$", text, re.M)
        lists = re.search(rf"^!{res[1]} = !\{{(.*)\}}$", text, re.M)
        srv, uav, cbv, smp = [x.strip() for x in lists[1].split(",")]
        md = max(int(n) for n in re.findall(r"^!(\d+) = ", text, re.M)) + 1
        if srv == "null":
            srv_ids, srv_line = [], None
        else:
            srv_line = re.search(rf"^{re.escape(srv)} = !\{{(.*)\}}$", text, re.M)
            srv_ids = [int(re.search(rf"^{re.escape(e.strip())} = !\{{i32 (\d+),", text, re.M)[1])
                       for e in srv_line[1].split(",")]
        entry = (f"!{md} = !{{i32 {max(srv_ids, default=-1) + 1}, %struct.RaytracingAccelerationStructure* undef, "
                 f"!\"\", i32 {MOD_SPACE}, i32 0, i32 1, i32 16, i32 0, !{md + 1}}}\n!{md + 1} = !{{i32 0, i32 4}}\n")
        if srv_line is None:
            text = text.replace(lists[0], f"!{res[1]} = !{{!{md + 2}, {uav}, {cbv}, {smp}}}")
            text += entry + f"!{md + 2} = !{{!{md}}}\n"
        else:
            text = text.replace(srv_line[0], f"{srv} = !{{{srv_line[1]}, !{md}}}")
            text += entry
        # ... and the shader flags declare inline ray tracing (RaytracingTier1_1)
        flags = list(re.finditer(r"!\{i32 0, i64 (\d+)", text))
        need(len(flags) == 1, f"{len(flags)} shader-flag entries")
        f = flags[0]
        text = text[:f.start(1)] + str(int(f[1]) | 0x2000000) + text[f.end(1):]

    return text


def trace_segment(ir: Ir, height: str):
    """From a march sample's height above the planet, recover the segment's
    ends (t0, t1), the ray's origin and direction and the planet radius R by
    data flow. The sample sits at the segment's midpoint."""
    kind, length, radius = ir.binops.get(height, (None, None, None))
    need(kind == "fsub" and length in ir.unary and ir.unary[length][0] == OP_SQRT,
         f"{height}: height is not |p| - R")
    square_sum = ir.unary[length][1]
    if square_sum in ir.dot3:
        a = ir.dot3[square_sum]
        need(a[:3] == a[3:], f"{square_sum}: not dot(p, p)")
        comps = list(a[:3])
    else:
        def leaves(v):
            if v in ir.binops and ir.binops[v][0] == "fadd":
                return leaves(ir.binops[v][1]) + leaves(ir.binops[v][2])
            if v in ir.fmad and ir.fmad[v][0] == ir.fmad[v][1]:  # FMad(x, x, rest)
                return [("sq", ir.fmad[v][0])] + leaves(ir.fmad[v][2])
            return [("leaf", v)]
        comps = []
        for k, v in leaves(square_sum):
            if k == "sq":
                comps.append(v)
            else:
                need(v in ir.binops and ir.binops[v][0] == "fmul" and ir.binops[v][1] == ir.binops[v][2],
                     f"{v}: not a squared component")
                comps.append(ir.binops[v][1])
    # (2 components where the march starts on an axis with a zero direction
    # component there, as the LUT passes do: the zero one is folded away)
    need(len(comps) in (2, 3), f"{square_sum}: {len(comps)} components")

    # Each component is o + tmid * d, in either compiled form, or just tmid * d
    # (a zero origin component).
    terms = []
    for c in comps:
        if c in ir.fmad:
            x, y, o = ir.fmad[c]
            terms.append(({x, y}, o))
        elif c in ir.binops and ir.binops[c][0] == "fmul":
            terms.append(({ir.binops[c][1], ir.binops[c][2]}, 0.0))
        else:
            need(c in ir.binops and ir.binops[c][0] == "fadd", f"{c}: component is not o + t * d")
            _, a, b = ir.binops[c]
            prod, o = (a, b) if a in ir.binops and ir.binops[a][0] == "fmul" else (b, a)
            need(prod in ir.binops and ir.binops[prod][0] == "fmul", f"{c}: no t * d")
            terms.append(({ir.binops[prod][1], ir.binops[prod][2]}, o))
    common = set.intersection(*(f for f, _ in terms))
    need(len(common) == 1, "no common ray parameter in the sample position")
    tmid = common.pop()
    direction = [(f - {tmid}).pop() for f, _ in terms] + [0.0] * (3 - len(terms))
    origin = [o for _, o in terms] + [0.0] * (3 - len(terms))

    if tmid in ir.fmad:
        x, y, t0 = ir.fmad[tmid]
        dt = x if y == "5.000000e-01" else y
    else:
        need(tmid in ir.binops and ir.binops[tmid][0] == "fadd", f"{tmid}: not t0 + dt / 2")
        _, a, b = ir.binops[tmid]
        half, t0 = (a, b) if a in ir.binops and "5.000000e-01" in ir.binops[a][1:] else (b, a)
        need(half in ir.binops and "5.000000e-01" in ir.binops[half][1:], f"{tmid}: no dt * 0.5")
        dt = [op for op in ir.binops[half][1:] if op != "5.000000e-01"][0]
    need(dt in ir.binops and ir.binops[dt][0] == "fsub" and ir.binops[dt][2] == t0, f"{dt}: not t1 - t0")
    return t0, ir.binops[dt][1], origin, direction, radius


def emit_boundary_layer_average(em: "Emitter", segment, midpoint_bl: str) -> str:
    """Boundary-layer density averaged over the march segment, as
    in our medium model: (F(h1) - F(h0)) / (h1 - h0)
    for F the layer's antiderivative over height; the midpoint value when the
    segment is nearly level."""
    t0, t1, origin, direction, radius = segment

    def height(t):
        q = [em.mad(t, direction[c], origin[c]) for c in range(3)]
        return em.sub(em.sqrt(em.mad(q[2], q[2], em.mad(q[1], q[1], em.mul(q[0], q[0])))), radius)

    h0, h1 = height(t0), height(t1)
    return emit_boundary_layer_between(em, h0, emit_boundary_layer_terms(em, h0),
                                       h1, emit_boundary_layer_terms(em, h1), midpoint_bl)


def emit_boundary_layer_terms(em: "Emitter", h: str) -> tuple[str, str]:
    """The parts of F(h) = T3 * (min(h, T1) - edge * tail) at one height:
    (min(h, T1), tail) with tail = ln(1 + e^-|x|) = SoftplusTail(x), x = (h - T1) / edge."""
    x = em.mul(em.sub(h, "%mod.T1"), "%mod.ied")
    log2_tail = em.unary(23, em.add(em.exp2(em.mul(em.unary(6, x), -LOG2E_F)), 1.0))  # log2(1 + e^-|x|)
    return em.min(h, "%mod.T1"), em.mul(log2_tail, 0.6931471805599453)


def emit_boundary_layer_between(em: "Emitter", h0: str, terms0, h1: str, terms1, midpoint_bl: str) -> str:
    """(F(h1) - F(h0)) / (h1 - h0), or the midpoint value when the segment is
    nearly level; the difference grouped as BoundaryLayerIntegralBetween in
    the model, so that nothing cancels."""
    dh = em.sub(h1, h0)
    sloped = em.cmp("ogt", em.unary(6, dh), 0.01)
    below = em.sub(terms1[0], terms0[0])
    difference = em.mul("%mod.T3", em.sub(below, em.mul("%mod.edge", em.sub(terms1[1], terms0[1]))))
    average = em.div(difference, em.select(sloped, dh, 1.0))
    return em.select(sloped, average, midpoint_bl)


def find_medium(ir: Ir, no_phase: bool = False):
    """Locate the inline medium by data flow: the march sites (densities,
    coefficient products, ozone, extinction sums, the march segment) and the
    HG phases. Returns (sites, phases). A pass that only computes extinction
    (the transmittance LUT) has no Mie scattering products; with no_phase
    (the transmittance and multiple-scattering LUTs) it needs no HG phase."""
    need(ir.atmo_handles, "no SkyAtmosphere cbuffer (208 bytes)")

    # ---- the inline medium: the Mie and Rayleigh density exponentials
    def density_exp(reg):
        found = []
        for name, (op, arg) in ir.unary.items():
            if op != OP_EXP or arg not in ir.binops:
                continue
            kind, a, b = ir.binops[arg]
            if kind != "fmul":
                continue
            for scale, other in ((a, b), (b, a)):
                if ir.atmo(scale) == (reg, 3):
                    found.append((name, other))
        return found

    mie = density_exp(4)
    ray = density_exp(5)
    need(mie, "no Mie density")
    sites = []
    for mie_exp, h2 in mie:
        kind, a, b = ir.binops.get(h2, (None, None, None))
        need(kind == "fmul" and LOG2E in (a, b), f"{mie_exp}: Mie density argument is not h * log2e")
        height = b if a == LOG2E else a
        ray_exp = [r for r, arg in ray if arg == h2]
        need(len(ray_exp) == 1, f"{mie_exp}: no matching Rayleigh density")
        sites.append({"mie": mie_exp, "h": height, "ray": ray_exp[0], "segment": trace_segment(ir, height)})

    def products(coeff_reg, factor, optional=False):
        """fmul(c<coeff_reg>.c, factor) for c = 0..2 -> {c: (product, coefficient)};
        with optional, {} where there are none."""
        out = {}
        for name, (kind, a, b) in ir.binops.items():
            if kind != "fmul":
                continue
            for coeff, other in ((a, b), (b, a)):
                rc = ir.atmo(coeff)
                if other == factor and rc is not None and rc[0] == coeff_reg and rc[1] < 3:
                    need(rc[1] not in out, f"two products of c{coeff_reg}.{rc[1]} and {factor}")
                    out[rc[1]] = (name, coeff)
        if optional and not out:
            return out
        need(sorted(out) == [0, 1, 2], f"products of c{coeff_reg} and {factor}: found {sorted(out)}")
        return out

    for site in sites:
        site["ray_p"] = products(1, site["ray"])
        site["mie_scat_p"] = products(2, site["mie"], optional=True)  # (none in an extinction-only pass)
        site["mie_ext_p"] = products(3, site["mie"])
        # Ozone: the products with c0.xyz after the Mie density.
        candidates = set()
        for k, a, b in ir.binops.values():
            if k == "fmul":
                for coeff, other in ((a, b), (b, a)):
                    if (ir.atmo(coeff) or (-1,))[0] == 0:
                        candidates.add(other)
        ozone = [c for c in candidates if c in ir.unary and ir.unary[c][0] == OP_SATURATE
                 and ir.defs[c] > ir.defs[site["mie"]]]
        need(ozone, f"{site['mie']}: no ozone density")
        site["ozo"] = min(ozone, key=lambda c: ir.defs[c])
        site["ozo_p"] = products(0, site["ozo"])

        # Extinction: the fadd tree holding the ozone, Rayleigh and Mie-extinction products.
        site["ext"] = {}
        for c in range(3):
            want = {site["ozo_p"][c][0], site["ray_p"][c][0], site["mie_ext_p"][c][0]}

            def leaves(v):
                if v in ir.binops and ir.binops[v][0] == "fadd":
                    return leaves(ir.binops[v][1]) | leaves(ir.binops[v][2])
                return {v}

            node = site["ozo_p"][c][0]
            while not want <= leaves(node):
                ups = [ir.def_name(i) for i in ir.users(node)
                       if DEFINE.match(ir.lines[i]) and ir.def_name(i) in ir.binops
                       and ir.binops[ir.def_name(i)][0] == "fadd"]
                need(len(ups) == 1, f"extinction sum for channel {c}: {len(ups)} fadd users of {node}")
                node = ups[0]
            need(leaves(node) == want, f"extinction sum for channel {c} has extra terms")
            site["ext"][c] = node
        site["c2g"] = site["mie_scat_p"][1][1] if site["mie_scat_p"] else None  # (else loaded by the patch)

    # ---- HG phase: (1 - g^2) / (sqrt(d) * 4pi * d), g = MiePhaseG (c1.w)
    g_values = {v for v in ir.extracts if ir.atmo(v) == (1, 3)}
    g2 = {n for n, (k, a, b) in ir.binops.items() if k == "fmul" and a == b and a in g_values}
    numer = {n for n, (k, a, b) in ir.binops.items()
             if k == "fsub" and b in g2 and not a.startswith("%") and float(a) == 1.0}
    phases = []
    for name, (kind, a, b) in ir.binops.items():
        if kind == "fdiv" and a in numer:
            # find cos(theta) in the denominator (its factors come in either order)
            two = find_operand(ir, b, lambda v: v in ir.binops and ir.binops[v][0] == "fmul"
                               and "2.000000e+00" in ir.binops[v][1:])
            need(two, f"{name}: HG phase without cos * 2")
            x = [op for op in ir.binops[two][1:] if op != "2.000000e+00"][0]
            if x in ir.binops and ir.binops[x][0] == "fmul" and set(ir.binops[x][1:]) & g_values:
                x = [op for op in ir.binops[x][1:] if op not in g_values][0]
            g_value = ir.binops[ir.binops[a][2]][1]  # 1 - g*g -> g*g -> g
            phases.append((name, x, g_value))
    need(phases or no_phase, "no HG phase")
    return sites, phases


def patch_medium(text: str, no_phase: bool = False, white_balance: bool = False, far_slice: bool = False,
                 dump: str | None = None) -> tuple[str, str]:
    """Our medium (MediumSample in the model) in a pass's inline atmosphere
    march, and its HG phases (T9, the backscatter lobe). Options for the sky
    passes we patch instead of replacing:
      no_phase       the pass has no HG phase (the transmittance LUT, and the
                     multiple-scattering LUT with its isotropic phase);
      white_balance  the daylight white balance on the stored colour (C22,
                     the transmittance LUT; see SUN_WHITE_BALANCE);
      far_slice      the camera volume's far-slice stretch (T12, apply_far_slice);
      dump           a capture dump on thread 0: "sky_atmosphere" (region A)
                     or "camera" (region B), as the add-on reads them."""
    ir = Ir(text)
    sites, phases = find_medium(ir, no_phase)

    # ---- rewrite. Work from the bottom up so line indices stay valid.
    edits = []  # (line index, replacement lines)

    def rename_def(line_index, new_name):
        line = ir.lines[line_index]
        old = ir.def_name(line_index)
        return old, line.replace(old + " = ", new_name + " = ", 1)

    for s, site in enumerate(sites):
        p = f"%mod.s{s}."
        # Mie density
        i = ir.defs[site["mie"]]
        old, stock_line = rename_def(i, p + "dms")
        h = site["h"]
        em = Emitter(f"s{s}a")
        site["bla"] = emit_boundary_layer_average(em, site["segment"], f"{p}bl")
        edits.append((i, [
            stock_line,
            f"  {p}blx0 = fsub fast float {h}, %mod.T1",
            f"  {p}blx = fmul fast float {p}blx0, %mod.blk",
            f"  {p}ble = call float @dx.op.unary.f32(i32 21, float {p}blx)",
            f"  {p}bld = fadd fast float {p}ble, 1.000000e+00",
            f"  {p}bl = fdiv fast float %mod.T3, {p}bld",
            *em.lines,
            f"  {p}ftx = fmul fast float {h}, %mod.ftk",
            f"  {p}fte = call float @dx.op.unary.f32(i32 21, float {p}ftx)",
            f"  {p}ft = fmul fast float %mod.T4, {p}fte",
            f"  {p}dmm0 = fadd fast float {site['bla']}, {p}ft",
            f"  {p}dmm1 = fsub fast float {p}dmm0, {p}dms",
            f"  {p}dmm = call float @dx.op.tertiary.f32(i32 46, float {p}dmm1, float %mod.mix, float {p}dms)",
            f"  {old} = select i1 %mod.off, float {p}dms, float {p}dmm",
        ]))
        # Rayleigh and ozone coefficients, lerped toward the reference by mix * T8
        for key, ref, tag in (("ray_p", REFERENCE_RAYLEIGH, "ray"), ("ozo_p", REFERENCE_OZONE, "ozo")):
            for c, (prod, coeff) in site[key].items():
                i = ir.defs[prod]
                new = f"{p}{tag}{c}"
                lhs, rhs = ir.lines[i].split(" = ", 1)
                edits.append((i, [
                    f"  {new}d = fsub fast float {f32(ref[c])}, {coeff}",
                    f"  {new} = call float @dx.op.tertiary.f32(i32 46, float {new}d, float %mod.ref, float {coeff})",
                    lhs + " = " + re.sub(re.escape(coeff) + r"\b", new, rhs),
                ]))
        # Aerosol colour: Mie scattering and extinction coefficients times the
        # Angstrom spectral factor (exactly 1 at mix 0)
        for key, tag in (("mie_scat_p", "mies"), ("mie_ext_p", "miee")):
            for c, (prod, coeff) in site[key].items():
                i = ir.defs[prod]
                new = f"{p}{tag}{c}"
                lhs, rhs = ir.lines[i].split(" = ", 1)
                edits.append((i, [
                    f"  {new} = fmul fast float {coeff}, %mod.spec{c}",
                    lhs + " = " + re.sub(re.escape(coeff) + r"\b", new, rhs),
                ]))
        # Brown-haze absorption added to the extinction, scaled by MieScattering.g
        # (loaded here in a pass that doesn't load it)
        first = min(ir.defs[site["ext"][c]] for c in range(3))
        c2g, load = site["c2g"], []
        if c2g is None:
            c2g = f"{p}c2g"
            load = [f"  {p}c2r = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, "
                    f"%dx.types.Handle {next(iter(ir.atmo_handles))}, i32 2)",
                    f"  {c2g} = extractvalue %dx.types.CBufRet.f32 {p}c2r, 1"]
        else:
            need(ir.defs[c2g] < first, "MieScattering.g is defined after the extinction sum")
        for c in range(3):
            i = ir.defs[site["ext"][c]]
            old, sum_line = rename_def(i, f"{p}ext{c}")
            pre = load + [f"  {p}bw0 = fmul fast float %mod.brown, {site['bla']}",
                          f"  {p}bw1 = fmul fast float {p}bw0, {c2g}"] if i == first else []
            edits.append((i, pre + [
                sum_line,
                f"  {p}bw{c}c = fmul fast float {p}bw1, {f32(BROWN_HAZE_TINT[c])}",
                f"  {old} = fadd fast float {p}ext{c}, {p}bw{c}c",
            ]))

    # HG phase: our g (T9 lerped in by mix, if set), plus the backscatter lobe;
    # the stock value is kept untouched for mix 0.
    g = BACKSCATTER_G
    four_pi = f32(4.0 * 3.14159265358979)
    for k, (name, cos, g_stock) in enumerate(phases):
        i = ir.defs[name]
        p = f"%mod.hg{k}."
        old, fwd_line = rename_def(i, p + "stock")
        edits.append((i, [
            fwd_line,
            f"  {p}gd = fsub fast float %mod.gt, {g_stock}",
            f"  {p}gm = call float @dx.op.tertiary.f32(i32 46, float {p}gd, float %mod.mix, float {g_stock})",
            f"  {p}g = select i1 %mod.gon, float {p}gm, float {g_stock}",
            f"  {p}g2 = fmul fast float {p}g, {p}g",
            f"  {p}fn = fsub fast float 1.000000e+00, {p}g2",
            f"  {p}fk = fmul fast float {p}g, -2.000000e+00",
            f"  {p}fd0 = fadd fast float {p}g2, 1.000000e+00",
            f"  {p}fd = call float @dx.op.tertiary.f32(i32 46, float {cos}, float {p}fk, float {p}fd0)",
            f"  {p}fs = call float @dx.op.unary.f32(i32 24, float {p}fd)",
            f"  {p}fsd = fmul fast float {p}fs, {p}fd",
            f"  {p}fq = fmul fast float {p}fsd, {four_pi}",
            f"  {p}fwd = fdiv fast float {p}fn, {p}fq",
            f"  {p}d = call float @dx.op.tertiary.f32(i32 46, float {cos}, float {f32(-2.0 * g)}, float {f32(1.0 + g * g)})",
            f"  {p}s = call float @dx.op.unary.f32(i32 24, float {p}d)",
            f"  {p}sd = fmul fast float {p}s, {p}d",
            f"  {p}q = fmul fast float {p}sd, {four_pi}",
            f"  {p}back = fdiv fast float {f32(1.0 - g * g)}, {p}q",
            f"  {p}diff = fsub fast float {p}back, {p}fwd",
            f"  {p}mixed = call float @dx.op.tertiary.f32(i32 46, float {p}diff, float %mod.w, float {p}fwd)",
            f"  {old} = select i1 %mod.off, float {p}stock, float {p}mixed",
        ]))

    extras = []
    if white_balance:
        edits.append(white_balance_edit(ir))
        extras.append("white balance")
    if far_slice:
        edits += far_slice_edits(ir)
        extras.append("far-slice stretch")
    for i, new_lines in sorted(edits, key=lambda e: -e[0]):
        ir.lines[i:i + 1] = new_lines
    if dump:
        append_sky_dump(ir, dump)
        extras.append(f"{dump} dump")

    text = finish(ir, cloud=white_balance, debug_uav=bool(dump))

    summary = (f"{len(sites)} medium site(s), {len(phases)} HG phase(s); "
               f"height {', '.join(s['h'] for s in sites)}" + (f"; {', '.join(extras)}" if extras else ""))
    return text, summary


# The daylight white balance (C22), as SunWhiteBalance in the model: MSFS's sun
# (the light block's c1 = (141102.8, 125271, 120217.7)) made white above the
# atmosphere, each channel <= 1; strength max(C22, 0) * T0.
SUN_WHITE_BALANCE = (120217.7 / 141102.8, 120217.7 / 125271.0, 1.0)
DEBUG_MAGIC = 0x4F4D5441  # 'ATMO', the debug buffer's header


def white_balance_edit(ir: Ir) -> tuple:
    """The transmittance LUT's store, with its colour times the white balance."""
    stores = [i for i, l in enumerate(ir.lines) if "@dx.op.textureStore.f32(i32 67," in l]
    need(len(stores) == 1, f"{len(stores)} texture stores")
    i = stores[0]
    m = re.match(r"(\s+call void @dx\.op\.textureStore\.f32\(i32 67, %dx\.types\.Handle \S+, i32 \S+, i32 \S+, "
                 r"i32 \S+, )float (\S+), float (\S+), float (\S+), float (\S+)(, i8 \d+\).*)$", ir.lines[i])
    need(m, "unexpected texture store")
    rgb = [m[2], m[3], m[4]]
    lines = ["  %mod.wb.s0 = call float @dx.op.binary.f32(i32 35, float %mod.C22, float 0.000000e+00)",
             "  %mod.wb.s = fmul fast float %mod.wb.s0, %mod.mix"]
    out = []
    for c in range(3):
        lines += [f"  %mod.wb.k{c} = fmul fast float %mod.wb.s, {f32(1.0 - SUN_WHITE_BALANCE[c])}",
                  f"  %mod.wb.w{c}0 = fsub fast float 1.000000e+00, %mod.wb.k{c}",
                  f"  %mod.wb.w{c} = call float @dx.op.unary.f32(i32 7, float %mod.wb.w{c}0)",
                  f"  %mod.wb.c{c} = fmul fast float {rgb[c]}, %mod.wb.w{c}"]
        out.append(f"%mod.wb.c{c}")
    alpha = out[rgb.index(m[5])] if m[5] in rgb else m[5]  # (stock writes red into alpha)
    lines.append(f"{m[1]}float {out[0]}, float {out[1]}, float {out[2]}, float {alpha}{m[6]}")
    return i, lines


FAR_CLOUD_DECK_KM = 2.0  # as kFarCloudDeck in the model


def far_slice_edits(ir: Ir) -> list:
    """The camera volume's slice depth t = slice^2 * 0.001 * range: for the
    last slice, with T12, stretched toward the farthest visible low cloud, the
    camera's horizon plus a cloud deck's horizon (as the model):
    t' = t + (max(t, far) - t) * sat(T12) * T0."""
    glob = {h for h, (_, size) in ir.cbs.items() if size == 4800}
    ranges = {v for v, (load, comp) in ir.extracts.items()
              if comp == 1 and load in ir.loads and ir.loads[load][0] in glob and ir.loads[load][1] == 10}
    cands = []
    for name, (kind, a, b) in ir.binops.items():
        if kind != "fmul" or not ({a, b} & ranges):
            continue
        x = b if a in ranges else a
        if x in ir.binops and ir.binops[x][0] == "fmul" and "0x3F50624DE0000000" in ir.binops[x][1:]:
            sq = [o for o in ir.binops[x][1:] if o != "0x3F50624DE0000000"][0]
            if sq in ir.binops and ir.binops[sq][0] == "fmul" and ir.binops[sq][1] == ir.binops[sq][2]:
                cands.append((name, ir.binops[sq][1]))
    need(len(cands) == 1, f"{len(cands)} slice depths")
    t, slice_ = cands[0]
    need(slice_ in ir.binops and ir.binops[slice_][0] == "fmul", f"{slice_}: slice is not a product")
    inv = [o for o in ir.binops[slice_][1:] if o in ir.extracts and ir.extracts[o][1] == 2]
    need(len(inv) == 1, f"{slice_}: no 1 / slices")
    # the camera position: t * dir_c + cam_c for the voxel's three components
    cam = []
    for i in ir.users(t):
        if not DEFINE.match(ir.lines[i]):
            continue
        prod = ir.def_name(i)
        if ir.binops.get(prod, ("",))[0] != "fmul":
            continue
        adds = [ir.def_name(j) for j in ir.users(prod) if DEFINE.match(ir.lines[j])
                and ir.binops.get(ir.def_name(j), ("",))[0] == "fadd"]
        if len(adds) == 1:
            cam.append([o for o in ir.binops[adds[0]][1:] if o != prod][0])
    need(len(cam) == 3, f"{t}: {len(cam)} camera position components")
    atmo = next(iter(ir.atmo_handles))
    i = ir.defs[t]
    q = "%mod.fs."
    em = Emitter("fs.e")
    radius = em.new(f"extractvalue %dx.types.CBufRet.f32 "
                    f"{em.new(f'call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {atmo}, i32 2)')}, 3")
    length = em.sqrt(em.mad(cam[2], cam[2], em.mad(cam[1], cam[1], em.mul(cam[0], cam[0]))))
    h = em.sub(length, radius)
    two_r = em.mul(radius, 2.0)
    far = em.add(em.sqrt(em.max(em.mul(h, em.add(two_r, h)), 0.0)),
                 em.sqrt(em.mul(em.add(two_r, FAR_CLOUD_DECK_KM), FAR_CLOUD_DECK_KM)))
    r0 = em.new("call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.ah, i32 0)")
    k = em.mul(em.sat(em.new(f"extractvalue %dx.types.CBufRet.f32 {r0}, 2")), "%mod.mix")  # T12 * T0
    last = em.cmp("ogt", em.add(slice_, inv[0]), 1.0)  # (z + 1.5) / slices > 1
    on = em.new(f"and i1 {last}, {em.cmp('ogt', k, 0.0)}")
    stretched = em.mad(em.sub(em.max(f"{q}t0", far), f"{q}t0"), k, f"{q}t0")
    old, line = ir.lines[i].split(" = ", 1)[0].strip(), ir.lines[i]
    return [(i, [line.replace(f"{old} = ", f"{q}t0 = ", 1), *em.lines,
                 f"  {old} = select i1 {on}, float {stretched}, float {q}t0"])]


# The registers the camera-volume dump holds (region B), as the add-on labels
# them: Global[10, 51, 52, 56, 92, 94, 117, 132, 134-136, 174-177], User[1].
CAMERA_DUMP_GLOBAL = (10, 51, 52, 56, 92, 94, 117, 132, 134, 135, 136, 174, 175, 176, 177)
DEBUG_REGION_SKY_ATMOSPHERE = 16
DEBUG_REGION_CAMERA = 256


def append_sky_dump(ir: Ir, kind: str) -> None:
    """On capture (DebugCapture bit 0), thread 0 writes the debug buffer's
    header and its region: "sky_atmosphere" the 13 SkyAtmosphere registers
    (region A), "camera" the camera volume's Global and User registers (region
    B: 16 counted, 21 written, the last 5 zero)."""
    text = "\n".join(ir.lines)
    rets = [i for i, l in enumerate(ir.lines) if l.strip() == "ret void"]
    need(len(rets) == 1, f"{len(rets)} returns")
    tids = [n for n, c in re.findall(r"^\s+(%[\w.]+) = call i32 @dx\.op\.threadId\.i32\(i32 93, i32 ([012])\)",
                                      text, re.M)]
    need(tids, "no thread id")
    lines = capture_flag_lines("sd", 1)
    zero = "%mod.sd.on"
    for k, t in enumerate(tids):
        lines.append(f"  %mod.sd.z{k} = icmp eq i32 {t}, 0")
        lines.append(f"  %mod.sd.a{k} = and i1 {zero}, %mod.sd.z{k}")
        zero = f"%mod.sd.a{k}"
    lines += [
        f"  br i1 {zero}, label %mod.sdb.w, label %mod.sdb.end",
        "",
        "mod.sdb.w:",
        "  %mod.sd.uh = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
        f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)",
        "  %mod.sd.u = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.sd.uh, "
        "%dx.types.ResourceProperties { i32 4107, i32 0 })",
    ]
    store_words("%mod.sd.u", "0", [str(DEBUG_MAGIC), "%mod.sd.stamp", "0", "0"], lines, "sd.h")
    t = "%dx.types.CBufRet.f32"
    if kind == "sky_atmosphere":
        store_words("%mod.sd.u", str(DEBUG_REGION_SKY_ATMOSPHERE), ["%mod.sd.stamp", "13", "0", "0"], lines, "sd.ra")
        store_cbuffer("%mod.sd.u", next(iter(ir.atmo_handles)), 13, DEBUG_REGION_SKY_ATMOSPHERE + 16, lines, "sd.reg")
    elif kind == "camera":
        glob = [h for h, (_, size) in ir.cbs.items() if size == 4800]
        user = [h for h, (_, size) in ir.cbs.items() if size == 10528]
        need(len(glob) == 1 and len(user) == 1, "no Global / User cbuffer")
        store_words("%mod.sd.u", str(DEBUG_REGION_CAMERA), ["%mod.sd.stamp", "16", "0", "0"], lines, "sd.rb")
        regs = [(glob[0], r) for r in CAMERA_DUMP_GLOBAL] + [(user[0], 1)]
        for n, (handle, r) in enumerate(regs):
            lines.append(f"  %mod.sd.c{n} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {handle}, i32 {r})")
            comps = []
            for c in range(4):
                lines.append(f"  %mod.sd.c{n}.{c} = extractvalue {t} %mod.sd.c{n}, {c}")
                comps.append(f"float %mod.sd.c{n}.{c}")
            lines.append(f"  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sd.u, "
                         f"i32 {DEBUG_REGION_CAMERA + 16 + 16 * n}, i32 undef, {', '.join(comps)}, i8 15, i32 4)")
        for n in range(len(regs), 21):
            lines.append(f"  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sd.u, "
                         f"i32 {DEBUG_REGION_CAMERA + 16 + 16 * n}, i32 undef, float 0.000000e+00, "
                         "float 0.000000e+00, float 0.000000e+00, float 0.000000e+00, i8 15, i32 4)")
    else:
        need(False, f"unknown dump {kind}")
    lines += ["  br label %mod.sdb.end", "", "mod.sdb.end:", "  ret void"]
    ir.lines[rets[0]:rets[0] + 1] = lines


class Emitter:
    """Straight-line IR with fresh %mod.<prefix><n> names. Operands are value
    names or Python floats (written as exact float literals)."""

    def __init__(self, prefix: str):
        self.prefix, self.count, self.lines = prefix, 0, []

    def new(self, rhs: str) -> str:
        name = f"%mod.{self.prefix}{self.count}"
        self.count += 1
        self.lines.append(f"  {name} = {rhs}")
        return name

    @staticmethod
    def lit(x) -> str:
        return x if isinstance(x, str) else f32(x)

    def binop(self, op, a, b):
        return self.new(f"{op} fast float {self.lit(a)}, {self.lit(b)}")

    def mul(self, a, b):
        return self.binop("fmul", a, b)

    def add(self, a, b):
        return self.binop("fadd", a, b)

    def sub(self, a, b):
        return self.binop("fsub", a, b)

    def div(self, a, b):
        return self.binop("fdiv", a, b)

    def mad(self, a, b, c):
        return self.new(f"call float @dx.op.tertiary.f32(i32 46, float {self.lit(a)}, float {self.lit(b)}, "
                        f"float {self.lit(c)})")

    def unary(self, op, a):
        return self.new(f"call float @dx.op.unary.f32(i32 {op}, float {self.lit(a)})")

    def exp2(self, a):
        return self.unary(21, a)

    def sqrt(self, a):
        return self.unary(24, a)

    def sat(self, a):
        return self.unary(7, a)

    def max(self, a, b):
        return self.new(f"call float @dx.op.binary.f32(i32 35, float {self.lit(a)}, float {self.lit(b)})")

    def min(self, a, b):
        return self.new(f"call float @dx.op.binary.f32(i32 36, float {self.lit(a)}, float {self.lit(b)})")

    def cmp(self, pred, a, b):
        return self.new(f"fcmp fast {pred} float {self.lit(a)}, {self.lit(b)}")

    def select(self, cond, a, b):
        return self.new(f"select i1 {cond}, float {self.lit(a)}, float {self.lit(b)}")

    def handle(self, binding, size):
        return self.new(f"call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle {binding}, "
                        f"%dx.types.ResourceProperties {{ i32 13, i32 {size} }})")

    def load(self, handle, reg):
        return self.new(f"call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, "
                        f"%dx.types.Handle {handle}, i32 {reg})")

    def ext(self, row, comp):
        return self.new(f"extractvalue %dx.types.CBufRet.f32 {row}, {comp}")


CLOUD_AP_SAMPLES = 10
CLOUD_AP_NEWTON_STEPS = 3
LOG2E_F = 1.4426950408889634


def emit_cloud_haze_colour(em: Emitter, u, v, depth_m, tgrey, global_binding, atmo_binding,
                           samples: int = CLOUD_AP_SAMPLES) -> list[str]:
    """Per-channel version of MSFS's grey aerial-perspective transmittance for a
    cloud at depth_m (metres) along the view ray through screen uv (u, v).

    Marches the optical depth of our medium from the
    camera to the cloud for its spectral shape, and scales that shape so the
    mean transmittance equals the volume's grey one: the volume's own
    (filtered) brightness is kept exactly and only the colour changes.
    Blended in by mix * T11; exactly tgrey when that is 0."""
    gh = em.handle(global_binding, 4800)
    ah = em.handle(atmo_binding, 208)
    g = {r: em.load(gh, r) for r in (117, 132, 134, 135, 136, 174, 175, 176, 177)}
    a = {r: em.load(ah, r) for r in range(8)}

    # View ray through (u, v), matching the camera volume's rays.
    cx = em.mad(u, 2.0, -1.0)
    cy = em.mad(v, -2.0, 1.0)
    hv = [em.mad(em.ext(g[r], 0), cx, em.mad(em.ext(g[r], 1), cy, em.ext(g[r], 3))) for r in (174, 175, 176, 177)]
    vr = [em.div(hv[k], hv[3]) for k in range(3)]
    d = []
    for j in range(3):
        acc = em.mul(vr[0], em.ext(g[134], j))
        acc = em.mad(vr[1], em.ext(g[135], j), acc)
        d.append(em.mad(vr[2], em.ext(g[136], j), acc))
    inv_len = em.div(1.0, em.sqrt(em.mad(d[2], d[2], em.mad(d[1], d[1], em.mul(d[0], d[0])))))
    d = [em.mul(x, inv_len) for x in d]
    radius = em.ext(a[2], 3)
    cam = [em.mul(em.ext(g[132], 0), 0.001),
           em.add(radius, em.max(em.mul(em.add(em.ext(g[132], 1), em.ext(g[117], 1)), 0.001), 0.0)),
           em.mul(em.ext(g[132], 2), 0.001)]

    # Medium coefficients, with the model's per-flight constants from the prologue.
    c0 = [em.ext(a[0], k) for k in range(3)]
    c1 = [em.ext(a[1], k) for k in range(3)]
    c3 = [em.ext(a[3], k) for k in range(3)]
    c2g = em.ext(a[2], 1)
    k4 = em.mul(em.ext(a[4], 3), LOG2E_F)
    k5 = em.mul(em.ext(a[5], 3), LOG2E_F)
    c6x, c6y, c6z = (em.ext(a[6], k) for k in range(3))
    c7x, c7y = (em.ext(a[7], k) for k in range(2))
    ray = [em.mad(em.sub(REFERENCE_RAYLEIGH[k], c1[k]), "%mod.ref", c1[k]) for k in range(3)]
    ozo = [em.mad(em.sub(REFERENCE_OZONE[k], c0[k]), "%mod.ref", c0[k]) for k in range(3)]
    mie = [em.mul(c3[k], f"%mod.spec{k}") for k in range(3)]
    brown = em.mul("%mod.brown", c2g)
    brk = [em.mul(brown, BROWN_HAZE_TINT[k]) for k in range(3)]

    dt = em.mul(em.mul(depth_m, 0.001), 1.0 / samples)

    def height_at(t):
        q = [em.mad(d[j], t, cam[j]) for j in range(3)]
        return em.sub(em.sqrt(em.mad(q[2], q[2], em.mad(q[1], q[1], em.mul(q[0], q[0])))), radius)

    # Segment ends, for the boundary layer averaged over each segment.
    edge_h = [height_at(em.mul(dt, float(i))) for i in range(samples + 1)]
    edge_f = [emit_boundary_layer_terms(em, hb) for hb in edge_h]
    tau = [None, None, None]
    for i in range(samples):
        s = em.mul(dt, i + 0.5)
        pos = [em.mad(d[j], s, cam[j]) for j in range(3)]
        r = em.sqrt(em.mad(pos[2], pos[2], em.mad(pos[1], pos[1], em.mul(pos[0], pos[0]))))
        h = em.sub(r, radius)
        dms = em.exp2(em.mul(h, k4))
        dr = em.exp2(em.mul(h, k5))
        bl_mid = em.div("%mod.T3", em.add(em.exp2(em.mul(em.sub(h, "%mod.T1"), "%mod.blk")), 1.0))
        bl = emit_boundary_layer_between(em, edge_h[i], edge_f[i], edge_h[i + 1], edge_f[i + 1], bl_mid)
        ft = em.mul("%mod.T4", em.exp2(em.mul(h, "%mod.ftk")))
        dm = em.select("%mod.off", dms, em.mad(em.sub(em.add(bl, ft), dms), "%mod.mix", dms))
        o = em.sat(em.select(em.cmp("olt", h, c6x), em.mad(c6z, h, c6y), em.mad(c7y, h, c7x)))
        for k in range(3):
            e = em.mul(dm, mie[k])
            e = em.mad(dr, ray[k], e)
            e = em.mad(o, ozo[k], e)
            e = em.mad(bl, brk[k], e)
            tau[k] = e if tau[k] is None else em.add(tau[k], e)

    # Spectral shape from the march: tau_c = k_c * tau with mean(k) = 1. The
    # amount comes from the volume: solve mean(exp(-k_c * t)) = T_grey for t
    # (Newton from t = -ln T_grey; the function is convex and decreasing and
    # the start is left of the root by Jensen, so it converges monotonically).
    # The volume's grey value is a blend of neighbouring froxels, which near
    # the horizon mixes rays that skim the clouds with rays that clear them;
    # tying the tint to it keeps the colour consistent with the dimming
    # instead of putting the exact ray's colour on a blended brightness.
    tau = [em.mul(tau[k], dt) for k in range(3)]
    tau_mean = em.mul(em.add(em.add(tau[0], tau[1]), tau[2]), 1.0 / 3.0)
    flat = em.cmp("olt", tau_mean, 1e-6)
    inv_mean = em.div(1.0, em.max(tau_mean, 1e-6))
    shape = [em.select(flat, 1.0, em.mul(tau[k], inv_mean)) for k in range(3)]
    target = em.max(tgrey, 1e-4)
    t = em.mul(em.unary(23, target), -0.6931471805599453)  # -ln(target), via log2
    for _ in range(CLOUD_AP_NEWTON_STEPS):
        e = [em.exp2(em.mul(em.mul(shape[k], t), -LOG2E_F)) for k in range(3)]
        f = em.sub(em.mul(em.add(em.add(e[0], e[1]), e[2]), 1.0 / 3.0), target)
        slope = em.mul(em.mad(shape[2], e[2], em.mad(shape[1], e[1], em.mul(shape[0], e[0]))), 1.0 / 3.0)
        t = em.max(em.add(t, em.div(f, em.max(slope, 1e-6))), 0.0)
    trans = [em.exp2(em.mul(em.mul(shape[k], t), -LOG2E_F)) for k in range(3)]

    weight = em.mul("%mod.mix", em.sat("%mod.T11"))
    on = em.cmp("ogt", weight, 0.0)
    return [em.select(on, em.mad(em.sub(trans[k], tgrey), weight, tgrey), tgrey) for k in range(3)]


def apply_cloud_ap(ir: Ir) -> str:
    """ch_volumetric_clouds_render: per-channel aerial-perspective transmittance.

    Stock applies the camera volume with one grey transmittance for all
    channels. We find that step by data flow and give each channel its own
    transmittance."""
    sized = lambda size: {h for h, (_, s) in ir.cbs.items() if s == size}
    glob, atmo = sized(4800), sized(208)
    need(glob and atmo, "no Global (4800 B) or SkyAtmosphere (208 B) cbuffer")
    global_binding = {ir.cbs[h][0] for h in glob}
    atmo_binding = {ir.cbs[h][0] for h in atmo}
    need(len(global_binding) == 1 and len(atmo_binding) == 1, "cbuffers bound more than once")

    # Depth: the distance divided by the AP range (Global[10].y), giving the slice coordinate w.
    ranges = {v for v, (load, comp) in ir.extracts.items()
              if comp == 1 and load in ir.loads and ir.loads[load][0] in glob and ir.loads[load][1] == 10}
    divs = [(n, a) for n, (k, a, b) in ir.binops.items() if k == "fdiv" and b in ranges]
    need(len(divs) == 1, f"{len(divs)} divisions by the AP range")
    div, depth = divs[0]
    roots = [n for n, (op, arg) in ir.unary.items() if op == OP_SQRT and arg == div]
    need(len(roots) == 1, "no AP slice coordinate")
    w = roots[0]

    # Screen uv: the two coordinates scaled just before the slice coordinate.
    w64 = [n for n, (k, a, b) in ir.binops.items() if k == "fmul" and w in (a, b) and "6.400000e+01" in (a, b)]
    need(len(w64) == 1, "no scaled slice coordinate")
    at = ir.defs[w64[0]]
    near = sorted((ir.defs[n], n) for n, (k, a, b) in ir.binops.items()
                  if k == "fmul" and "3.200000e+01" in (a, b) and 0 < at - ir.defs[n] <= 4)
    need(len(near) == 2, "no screen uv next to the slice coordinate")
    u, v = ([op for op in ir.binops[n][1:] if op != "3.200000e+01"][0] for _, n in near)

    # The near-camera fade and the four values it scales (rgb and alpha).
    fades = [n for n, (op, arg) in ir.unary.items()
             if op == OP_SATURATE and ir.defs[n] > ir.defs[w] and arg in ir.binops
             and find_operand(ir, arg, lambda x: x == w, limit=3)]
    need(len(fades) == 1, f"{len(fades)} near-camera fades")
    fade = fades[0]
    faded = [n for n, (k, a, b) in ir.binops.items() if k == "fmul" and fade in (a, b)]
    need(len(faded) == 4, f"{len(faded)} values scaled by the fade, expected rgb + alpha")
    greys = [n for n, (k, a, b) in ir.binops.items() if k == "fsub" and a == "1.000000e+00" and b in faded]
    need(len(greys) == 1, "no 1 - alpha")
    tgrey = greys[0]
    alpha = ir.binops[tgrey][2]
    inscatter = {}
    for n in faded:
        if n == alpha:
            continue
        avg = [op for op in ir.binops[n][1:] if op != fade][0]
        leaf = find_operand(ir, avg, lambda x: x in ir.resret, limit=16)
        need(leaf, f"{n}: in-scatter channel not traced to a volume sample")
        inscatter[ir.resret[leaf][1]] = n
    need(sorted(inscatter) == [0, 1, 2], f"in-scatter channels {sorted(inscatter)}")

    def phi_of(value):
        hits = [i for i in ir.users(value) if PHI.match(ir.lines[i])]
        need(len(hits) == 1, f"{value}: {len(hits)} phi uses")
        return hits[0]

    tphi_line = phi_of(tgrey)
    tphi = ir.def_name(tphi_line)
    sphi = {c: ir.def_name(phi_of(inscatter[c])) for c in range(3)}
    incoming = re.findall(r"\[ ([^,\]]+), (%[\w.]+) \]", ir.lines[tphi_line])
    need(len(incoming) == 2 and sum(val == tgrey for val, _ in incoming) == 1, "unexpected transmittance phi")

    # Users of the transmittance: cloud colour * T, then + in-scatter of that channel.
    uses = {}
    for i in ir.users(tphi):
        prod = ir.def_name(i) if DEFINE.match(ir.lines[i]) else None
        need(prod in ir.binops and ir.binops[prod][0] == "fmul", f"transmittance used outside a product: {prod}")
        channels = {c for j in ir.users(prod) if DEFINE.match(ir.lines[j])
                    for c, ph in sphi.items()
                    if ir.binops.get(ir.def_name(j), ("",))[0] == "fadd" and ph in ir.binops[ir.def_name(j)][1:]}
        need(len(channels) == 1, f"{prod}: channel not identified")
        uses[channels.pop()] = i
    need(sorted(uses) == [0, 1, 2], f"transmittance applied to channels {sorted(uses)}")

    em = Emitter("ca")
    out = emit_cloud_haze_colour(em, u, v, depth, tgrey, global_binding.pop(), atmo_binding.pop())
    last_phi = tphi_line
    while PHI.match(ir.lines[last_phi + 1]):
        last_phi += 1
    phis = []
    for c in range(3):
        pairs = ", ".join(f"[ {out[c] if val == tgrey else val}, {label} ]" for val, label in incoming)
        phis.append(f"  %mod.ca.t{c} = phi float {pairs}")

    edits = [(i, [re.sub(re.escape(tphi) + r"\b", f"%mod.ca.t{c}", ir.lines[i])]) for c, i in uses.items()]
    edits.append((last_phi, [ir.lines[last_phi]] + phis))
    edits.append((ir.defs[tgrey], [ir.lines[ir.defs[tgrey]]] + em.lines))
    for i, new_lines in sorted(edits, key=lambda e: -e[0]):
        ir.lines[i:i + 1] = new_lines
    return f"cloud AP colour: depth {depth}, uv {u} {v}, T {tgrey}; {CLOUD_AP_SAMPLES} samples"


def patch_cloud_ap(text: str) -> tuple[str, str]:
    ir = Ir(text)
    summary = apply_cloud_ap(ir)
    return finish(ir), summary


def linear_in(ir: Ir, value: str):
    """value = c + k * x for a literal c and k: (c, k, x), else None."""
    def scaled(v):
        if v in ir.binops and ir.binops[v][0] == "fmul":
            a, b = ir.binops[v][1:]
            for x, k in ((a, b), (b, a)):
                if x.startswith("%") and not k.startswith("%"):
                    return float(literal_value(k)), x
        return 1.0, v

    if value in ir.fmad:
        x, k, c = ir.fmad[value]
        if not k.startswith("%") and not c.startswith("%"):
            return float(literal_value(c)), float(literal_value(k)), x
        return None
    if value not in ir.binops or ir.binops[value][0] not in ("fadd", "fsub"):
        return None
    kind, a, b = ir.binops[value]
    if kind == "fadd":
        c, rest = (a, b) if not a.startswith("%") else (b, a)
        if c.startswith("%"):
            return None
        k, x = scaled(rest)
        return float(literal_value(c)), k, x
    if a.startswith("%") or not b.startswith("%"):
        return None
    k, x = scaled(b)
    return float(literal_value(a)), -k, x


def hg_lobes(ir: Ir) -> dict:
    """Henyey-Greenstein lobes written as (1 - g^2) / 4pi / exp2(1.5 * log2(1 + g^2 - 2 g cos)):
    value -> (g, cos)."""
    lobes = {}
    for name, (kind, num, den) in ir.binops.items():
        if kind != "fdiv" or num.startswith("%") or den not in ir.unary or ir.unary[den][0] != OP_EXP:
            continue
        power = ir.unary[den][1]
        if power not in ir.binops or ir.binops[power][0] != "fmul" or "1.500000e+00" not in ir.binops[power][1:]:
            continue
        lg = [op for op in ir.binops[power][1:] if op != "1.500000e+00"][0]
        if lg not in ir.unary or ir.unary[lg][0] != OP_LOG2 or not (base := linear_in(ir, ir.unary[lg][1])):
            continue
        c, k, cos = base
        g = -k / 2.0
        if abs(c - (1 + g * g)) < 1e-4 and abs(float(literal_value(num)) - (1 - g * g) / (4 * math.pi)) < 1e-5:
            lobes[name] = (g, cos)
    return lobes


def find_phase_blends(ir: Ir) -> list:
    """The clouds' equal-weight blends of a backward and a forward HG lobe on
    the same cos: [(blend, cos, forward lobe, g_fwd, g_back)]."""
    lobes = hg_lobes(ir)
    blends = []
    for name, (kind, a, b) in ir.binops.items():
        # 0.5 * (A - B) + B
        if kind != "fadd":
            continue
        for half, base in ((a, b), (b, a)):
            if base not in lobes or half not in ir.binops or ir.binops[half][0] != "fmul":
                continue
            if "5.000000e-01" not in ir.binops[half][1:]:
                continue
            diff = [op for op in ir.binops[half][1:] if op != "5.000000e-01"][0]
            if diff not in ir.binops or ir.binops[diff][0] != "fsub" or ir.binops[diff][2] != base:
                continue
            other = ir.binops[diff][1]
            if other not in lobes or lobes[other][1] != lobes[base][1]:
                continue
            (g_a, cos), (g_b, _) = lobes[other], lobes[base]
            need(g_a < 0 < g_b or g_b < 0 < g_a, f"{name}: blend of two lobes on the same side")
            forward = base if g_b > g_a else other
            blends.append((name, cos, forward, max(g_a, g_b), min(g_a, g_b)))
    need(blends, "no 50/50 HG blend in the cloud phase")
    return blends


def apply_cloud_phase(ir: Ir) -> str:
    """ch_volumetric_clouds_render: a forward lobe in the clouds' sun and moon phase.

    Stock mixes a backward and a forward HG lobe in equal parts, then blends
    the result toward isotropic as the sample's sun visibility drops. We move
    part of the forward lobe's weight to HG(C1):
    P' = P + w * (HG(C1, cos) - HG(g, cos)), w = C0 * C2 in [0, 0.5], so the
    phase stays normalised and the backward lobe is unchanged. Exactly stock
    when w = 0."""
    blends = find_phase_blends(ir)
    edits = []
    for k, (name, cos, forward, g_fwd, g_back) in enumerate(blends):
        i = ir.defs[name]
        p = f"%mod.cp{k}."
        old, stock_line = ir.def_name(i), ir.lines[i].replace(ir.def_name(i) + " = ", p + "stock = ", 1)
        edits.append((i, [
            stock_line,
            f"  {p}d = call float @dx.op.tertiary.f32(i32 46, float {cos}, float %mod.cgk, float %mod.cgd)",
            f"  {p}s = call float @dx.op.unary.f32(i32 24, float {p}d)",
            f"  {p}q = fmul fast float {p}d, {p}s",
            f"  {p}hg = fdiv fast float %mod.cgn, {p}q",
            f"  {p}diff = fsub fast float {p}hg, {forward}",
            f"  {p}new = call float @dx.op.tertiary.f32(i32 46, float {p}diff, float %mod.cw, float {p}stock)",
            f"  {old} = select i1 %mod.coff, float {p}stock, float {p}new",
        ]))
    for i, new_lines in sorted(edits, key=lambda e: -e[0]):
        ir.lines[i:i + 1] = new_lines
    lobes_text = ", ".join(f"{g_back:+.2f}/{g_fwd:+.2f}" for _, _, _, g_fwd, g_back in blends)
    return f"cloud phase: {len(blends)} blend(s) (back/forward g {lobes_text})"


INV_4PI = "0x3FB45F3060000000"      # 1 / 4pi as a float literal
NEG_INV_4PI = "0xBFB45F3060000000"
SOFT_EDGE = "0x3FE3333340000000"    # the soft transmittance's offset (used to find it)


def find_cloud_ms(ir: Ir) -> dict:
    """The clouds' multiple-scattering stand-ins:

    - the sun phase's blend toward isotropic as the sample's sun visibility s
      drops, per colour channel (3 where rain has rainbows, else 1);
    - every use of the soft transmittance (the shadow volume, the short march
      toward the sun).

    Returns {"blend", "cos", "base", "s", "s2", "adds": [1 or 3], "softs": [(x, quarter)]}."""
    sun = []
    for name, cos, *_ in find_phase_blends(ir):
        for i in ir.users(name):
            if not DEFINE.match(ir.lines[i]):
                continue
            user = ir.def_name(i)
            if ir.binops.get(user, ("",))[0] == "fadd" and NEG_INV_4PI in ir.binops[user][1:]:
                sun.append((name, cos, user))
    need(len(sun) == 1, f"{len(sun)} sun phases blended toward 1/4pi")
    blend, cos, base = sun[0]

    # each channel's product with s^2, and the 1/4pi added back
    def users_of(value, kind):
        return [ir.def_name(i) for i in ir.users(value) if DEFINE.match(ir.lines[i])
                and ir.binops.get(ir.def_name(i), ("",))[0] == kind]
    sums = users_of(base, "fadd") or [base]
    products = [p for s in sums for p in users_of(s, "fmul")]
    # three colour channels where rain carries rainbows, else one grey phase
    need(len(products) in (1, 3), f"{len(products)} products of the sun phase, expected 1 or 3 channels")
    s2s = {[op for op in ir.binops[p][1:] if op not in sums][0] for p in products}
    need(len(s2s) == 1, "the channels are not scaled by one s^2")
    s2 = s2s.pop()
    need(s2 in ir.binops and ir.binops[s2][0] == "fmul" and ir.binops[s2][1] == ir.binops[s2][2],
         f"{s2}: not s * s")
    s = ir.binops[s2][1]
    need(s in ir.unary and ir.unary[s][0] == OP_SATURATE, f"{s}: s is not saturated")
    # (apply_cloud_shape's glory also takes s^2, and with it the (1 - C3) below)
    need(all(DEFINE.match(ir.lines[i]) and (ir.def_name(i) in products or ir.def_name(i).startswith("%mod.gl."))
             for i in ir.users(s2)), f"{s2}: used outside the sun phase")
    adds = [a for p in products for a in users_of(p, "fadd") if INV_4PI in ir.binops[a][1:]]
    need(len(adds) == len(products), f"{len(adds)} of {len(products)} channels add 1/4pi back")

    # the soft transmittance (see SOFT_EDGE)
    softs = []
    for name, (kind, a, b) in ir.binops.items():
        if kind != "fsub" or a != SOFT_EDGE or b not in ir.binops or ir.binops[b][0] != "fdiv":
            continue
        x, den = ir.binops[b][1:]
        if den not in ir.binops or ir.binops[den][0] != "fadd" or "1.000000e+00" not in ir.binops[den][1:]:
            continue
        quarter = [op for op in ir.binops[den][1:] if op != "1.000000e+00"][0]
        if quarter in ir.binops and ir.binops[quarter][0] == "fmul" and \
                set(ir.binops[quarter][1:]) == {x, "2.500000e-01"}:
            softs.append((x, quarter))
    need(softs, "no soft transmittance")
    return {"blend": blend, "cos": cos, "base": base, "s": s, "s2": s2, "adds": adds, "softs": softs}


def apply_cloud_ms(ir: Ir) -> str:
    """ch_volumetric_clouds_render: tunable multiple scattering (C3-C5).

    MSFS blends the sun phase toward isotropic as the sample's sun visibility
    s drops, and approximates multiple scattering with a soft transmittance
    that keeps some light deep inside. Generalised, each exactly stock at 0:
      C3: share of multiply scattered light already at sunlit edges,
          P = (P_mix - P_ms) s^2 (1 - C3) + P_ms;
      C4: g of the multiply scattered light, P_ms = HG(C4) (isotropic in stock);
      C5: deep-interior sunlight, the floor of that transmittance: its depth
          constant is set so that the floor equals C5.
    Must run before apply_cloud_phase, which redefines the blend."""
    found = find_cloud_ms(ir)
    edits = []
    # P_ms from the sun's cos, before the blend's first use
    i = ir.defs[found["base"]]
    p = "%mod.ms."
    cos = found["cos"]
    edits.append((i, [
        f"  {p}d = call float @dx.op.tertiary.f32(i32 46, float {cos}, float %mod.cmgk, float %mod.cmgd)",
        f"  {p}sq = call float @dx.op.unary.f32(i32 24, float {p}d)",
        f"  {p}q = fmul fast float {p}d, {p}sq",
        f"  {p}hg = fdiv fast float %mod.cmgn, {p}q",
        f"  {p}pms = select i1 %mod.cmgoff, float {INV_4PI}, float {p}hg",
        ir.lines[i].replace(f"fadd fast float {found['blend']}, {NEG_INV_4PI}",
                            f"fsub fast float {found['blend']}, {p}pms"),
    ]))
    need(edits[-1][1][-1] != ir.lines[i], f"{found['base']}: unexpected form {ir.lines[i].strip()}")
    for a in found["adds"]:
        j = ir.defs[a]
        new = ir.lines[j].replace(INV_4PI, f"{p}pms")
        need(new != ir.lines[j], f"{a}: unexpected form")
        edits.append((j, [new]))
    # s^2 (1 - C3)
    j = ir.defs[found["s2"]]
    s2 = found["s2"]
    edits.append((j, [
        ir.lines[j].replace(f"{s2} = ", f"{p}s2 = ", 1),
        f"  {p}s2k = fmul fast float {p}s2, %mod.cmsk",
        f"  {s2} = select i1 %mod.cmsoff, float {p}s2, float {p}s2k",
    ]))
    # the soft transmittance's floor
    for k, (x, quarter) in enumerate(found["softs"]):
        j = ir.defs[quarter]
        edits.append((j, [ir.lines[j].replace("2.500000e-01", "%mod.cfk")]))
    for j, new_lines in sorted(edits, key=lambda e: -e[0]):
        ir.lines[j:j + 1] = new_lines
    return f"cloud multiple scattering: sun phase {found['blend']}, {len(found['softs'])} soft transmittance(s)"


EDGE_SLOPE = "0xBFF2B28400000000"  # the boundary ramp's slope (used to find it)


def find_cloud_edges(ir: Ir) -> dict:
    """The clouds' density ramp across each boundary, one per march: the view
    march first, then the sun and moon marches where present. Also the view
    march's depth into the cloud in metres (the numerator of the ramp's
    input). Returns {"ramps": [(x, sat)], "depth"}."""
    ramps = []
    for name, (op, arg) in ir.unary.items():
        if op != OP_SATURATE or arg not in ir.binops:
            continue
        kind, a, b = ir.binops[arg]
        if kind != "fsub" or a != "1.500000e+00" or b not in ir.unary or ir.unary[b][0] != OP_EXP:
            continue
        m = ir.unary[b][1]
        if m in ir.binops and ir.binops[m][0] == "fmul" and EDGE_SLOPE in ir.binops[m][1:]:
            ramps.append((ir.defs[name], [o for o in ir.binops[m][1:] if o != EDGE_SLOPE][0], name))
    need(ramps, "no cloud boundary ramp")
    ramps.sort()
    x = ramps[0][1]
    kind, depth, _ = ir.binops.get(x, ("", "", ""))
    need(kind == "fdiv" and depth in ir.binops and ir.binops[depth][0] == "fmul"
         and "8.000000e+03" in ir.binops[depth][1:], f"{x}: no view-march depth")
    return {"ramps": [(x, name) for _, x, name in ramps], "depth": depth}


VOLUME_HEIGHT_SCALE = "0x3F10624DE0000000"  # the weather volume's altitude scale (used to find the sample altitude)
OPTICAL_REF = 0.01        # 1/m: the cloud extinction left unchanged by the profile exponent (B = 0.1, a cumulus core)
OPTICAL_ICE_DEFAULT_M = 6000.0
OPTICAL_ICE_BLEND_M = 3000.0  # the ice blend: from C18 - 3 km (liquid) to C18 (MSFS's profile)


def find_view_extinction(ir: Ir) -> dict:
    """The view march's cloud extinction: the product of its boundary ramp
    (the first from find_cloud_edges) and the cloud's extinction. Also the
    sample's altitude h, from the weather-volume lookup just before it."""
    edges = find_cloud_edges(ir)
    ramp = edges["ramps"][0][1]
    users = [n for n, (kind, a, b) in ir.binops.items() if kind == "fmul" and ramp in (a, b)]
    need(len(users) == 1, f"{ramp}: {len(users)} products with the view ramp")
    prod = users[0]
    _, a, b = ir.binops[prod]
    sigma = b if a == ramp else a
    need(sigma.startswith("%") and ir.defs[sigma] < ir.defs[prod], f"{prod}: no cloud extinction operand")
    heights = []
    for n, (kind, a, b) in ir.binops.items():
        if kind != "fmul" or VOLUME_HEIGHT_SCALE not in (a, b):
            continue
        m = a if b == VOLUME_HEIGHT_SCALE else b
        line = ir.lines[ir.defs[m]] if m in ir.defs else ""
        mm = re.match(rf"\s+{re.escape(m)} = call float @dx\.op\.binary\.f32\(i32 35, float (\S+), float (\S+)\)", line)
        if mm and "0.000000e+00" in (mm[1], mm[2]):
            h = mm[2] if mm[1] == "0.000000e+00" else mm[1]
            if h.startswith("%") and ir.defs[h] < ir.defs[prod]:
                heights.append((ir.defs[n], h))
    need(heights, "no weather-volume altitude lookup before the view extinction")
    # the view march's: the last lookup before the product
    h = max(heights)[1]
    return {"prod": prod, "sigma": sigma, "height": h}


def apply_cloud_optical(ir: Ir) -> str:
    """ch_volumetric_clouds_render: the clouds' optical thickness (C16-C19),
    exactly stock while C16 <= 0.

    MSFS's cloud extinction is about 0.005-0.013/m in cumulus cores and much
    lower near bases, where real water clouds have 0.02-0.1/m, so bases and
    edges are see-through and an overcast lets the sun glow through. For liquid clouds the view march uses instead
        sigma' = C16 * 0.01 * (sigma / 0.01)^C17
    (C16: optical thickness at a cumulus core, 1 = stock there; C17: the
    profile exponent, 1 if <= 0 = stock profile, 1/3 ~ adiabatic: extinction
    growing as (height above base)^(2/3)). Toward the ice altitude C18 (m,
    6000 if <= 0), blended over the 3 km below it, clouds return to MSFS's
    profile scaled by C19 (1 if <= 0): cirrus stays thin, and thin mid-level
    sheets keep their translucent, soft edges instead of turning into opaque
    sheets with cut-out holes. Only the view march: the
    lighting (MSFS's light volume and short sun march) is unchanged, so the
    visible surface gets more solid without darkening the cloud."""
    v = find_view_extinction(ir)
    prod, sigma, h = v["prod"], v["sigma"], v["height"]
    i = ir.defs[prod]
    lines = [
        f"  %mod.op.r = fmul fast float {sigma}, {f32(1.0 / OPTICAL_REF)}",
        "  %mod.op.l = call float @dx.op.unary.f32(i32 23, float %mod.op.r)",
        "  %mod.op.e = fmul fast float %mod.op.l, %mod.cog",
        "  %mod.op.p = call float @dx.op.unary.f32(i32 21, float %mod.op.e)",
        "  %mod.op.w = fmul fast float %mod.op.p, %mod.cok",
        f"  %mod.op.ih0 = fsub fast float {h}, %mod.coh",
        f"  %mod.op.ih = fadd fast float %mod.op.ih0, {f32(OPTICAL_ICE_BLEND_M)}",
        f"  %mod.op.iy = fmul fast float %mod.op.ih, {f32(1.0 / OPTICAL_ICE_BLEND_M)}",
        "  %mod.op.iw = call float @dx.op.unary.f32(i32 7, float %mod.op.iy)",
        f"  %mod.op.iv = fmul fast float {sigma}, %mod.coki",
        "  %mod.op.d = fsub fast float %mod.op.iv, %mod.op.w",
        "  %mod.op.m = call float @dx.op.tertiary.f32(i32 46, float %mod.op.d, float %mod.op.iw, float %mod.op.w)",
        f"  %mod.op.s = select i1 %mod.cooff, float {sigma}, float %mod.op.m",
    ]
    _, a, b = ir.binops[prod]
    new = ir.lines[i].replace(f", {sigma}", ", %mod.op.s", 1) if b == sigma else \
        ir.lines[i].replace(f"float {sigma},", "float %mod.op.s,", 1)
    need(new != ir.lines[i], f"{prod}: couldn't substitute the extinction")
    ir.lines[i:i + 1] = lines + [new]
    return f"cloud optical thickness: {prod} = ramp * {sigma}, altitude {h}"


DETAIL_AMP = "0x3FA99999A0000000"      # the 1 km detail octave's amplitude (used to find it)
DETAIL_SCALE = "0x3F50624DE0000000"    # the octave's coordinate scale (used to find it)
DETAIL_BASE_DEPTH_M = 300.0
DETAIL_FINE_PERIOD_M = 350.0
PROFILE_SCALE_M = 1622.2               # altitude scale of the weather volume's density profile
PROFILE_GROWTH_M = 3000.0              # growth depth of that profile above a cloud base (both inverted
                                       # for the height above a base)


def find_detail_sites(ir: Ir) -> list:
    """The clouds' 1 km detail octave, one per march, where it is added to the
    shape. For each: the octave's product, its noise sample, coordinates and
    lod, and the march's weather-volume sample and altitude h."""
    volumes = []
    for name, (kind, a, b) in ir.binops.items():
        if kind != "fmul" or VOLUME_HEIGHT_SCALE not in (a, b):
            continue
        m = a if b == VOLUME_HEIGHT_SCALE else b
        mm = re.match(rf"\s+{re.escape(m)} = call float @dx\.op\.binary\.f32\(i32 35, float (\S+), float (\S+)\)",
                      ir.lines[ir.defs[m]] if m in ir.defs else "")
        if not mm or "0.000000e+00" not in (mm[1], mm[2]):
            continue
        h = mm[2] if mm[1] == "0.000000e+00" else mm[1]
        roots = [n for n, (op, arg) in ir.unary.items() if op == OP_SQRT and arg == name]
        for r in roots:
            for i in ir.users(r):
                sm = re.match(rf"\s+({V}) = call %dx\.types\.ResRet\.f32 @dx\.op\.sampleLevel\.f32\(i32 62, "
                              rf"%dx\.types\.Handle \S+, %dx\.types\.Handle \S+, float \S+, float \S+, float "
                              rf"{re.escape(r)},", ir.lines[i])
                if sm:
                    volumes.append((i, sm[1], h))
    sites = []
    for x, (kind, a, b) in ir.binops.items():
        if kind != "fmul" or DETAIL_AMP not in (a, b):
            continue
        A = a if b == DETAIL_AMP else b
        if A not in ir.binops or ir.binops[A][0] != "fadd" or "-7.500000e-01" not in ir.binops[A][1:]:
            continue
        n = [o for o in ir.binops[A][1:] if o != "-7.500000e-01"][0]
        if n not in ir.resret or ir.resret[n][1] != 0:
            continue
        sample = ir.resret[n][0]
        sm = re.match(rf"\s+{re.escape(sample)} = call %dx\.types\.ResRet\.f32 @dx\.op\.sampleLevel\.f32\(i32 62, "
                      rf"%dx\.types\.Handle ({V}), %dx\.types\.Handle ({V}), float ({V}), float ({V}), float ({V}), "
                      rf"float undef, i32 0, i32 0, i32 0, float ({V})\)", ir.lines[ir.defs[sample]])
        if not sm or not all(c in ir.binops and DETAIL_SCALE in ir.binops[c][1:] for c in (sm[3], sm[4], sm[5])):
            continue
        adds = [u for u, (k2, p, q) in ir.binops.items() if k2 == "fadd" and x in (p, q)]
        if len(adds) != 1:
            continue
        before = [v for v in volumes if v[0] < ir.defs[x]]
        if not before:
            continue
        _, vol, h = max(before)
        sites.append({"x": x, "A": A, "s2": adds[0], "tex": sm[1], "smp": sm[2], "coords": (sm[3], sm[4], sm[5]),
                      "lod": sm[6], "vol": vol, "h": h})
    sites.sort(key=lambda s: ir.defs[s["x"]])
    return sites


def apply_cloud_detail(ir: Ir) -> str:
    """ch_volumetric_clouds_render: the clouds' surface detail (C28-C31),
    exactly stock while C28 and C30 are 0.

    MSFS erodes every cloud surface with one noise at two scales; its 1 km
    octave makes round 100-250 m puffs everywhere, which show as "popcorn"
    on cloud bases once the clouds are solid (C16, C17). Real bases are flat
    and smooth, tops billow at many scales.
      - C28 (0..1): the 1 km octave fades out toward cloud bases, over the
        lowest C29 m (300 if 0) above each base. The height above the base
        comes from inverting the weather volume's density profile
        (PROFILE_SCALE_M, PROFILE_GROWTH_M). In every march, so
        self-shadowing matches.
      - C30 (0..0.1): a finer octave of the same noise at a C31 m period
        (350 if 0), billows on the billows, in the view march only, faded at
        bases like the 1 km octave."""
    sites = find_detail_sites(ir)
    if not sites:  # variants without the 1 km octave: no puffs to smooth
        return "cloud detail: none (no 1 km octave)"
    edits = []
    for i, site in enumerate(sites):
        p = f"%mod.bd{i}."
        x = site["x"]
        j = ir.defs[x]
        lines = [
            f"  {p}y = extractvalue %dx.types.ResRet.f32 {site['vol']}, 1",
            f"  {p}b0 = call float @dx.op.tertiary.f32(i32 46, float {p}y, float 2.000000e+00, float -1.000000e+00)",
            f"  {p}B = fmul fast float {p}b0, {p}b0",
            f"  {p}e = fmul fast float {site['h']}, {f32(1.4426950408889634 / PROFILE_SCALE_M)}",
            f"  {p}ex = call float @dx.op.unary.f32(i32 21, float {p}e)",
            f"  {p}r0 = fmul fast float {p}B, {p}ex",
            f"  {p}r1 = fmul fast float {p}r0, 5.000000e-01",
            f"  {p}r = call float @dx.op.binary.f32(i32 36, float {p}r1, float {f32(0.99)})",
            f"  {p}q = fsub fast float 1.000000e+00, {p}r",
            f"  {p}l = call float @dx.op.unary.f32(i32 23, float {p}q)",
            f"  {p}nl = fmul fast float {p}l, {f32(-0.6931471805599453)}",
            f"  {p}nl1 = call float @dx.op.binary.f32(i32 35, float {p}nl, float 0.000000e+00)",
            f"  {p}sq = call float @dx.op.unary.f32(i32 24, float {p}nl1)",
            f"  {p}D = fmul fast float {p}sq, {f32(PROFILE_GROWTH_M)}",
            f"  {p}t0 = fmul fast float {p}D, %mod.cbdi",
            f"  {p}t = call float @dx.op.unary.f32(i32 7, float {p}t0)",
            f"  {p}t2 = fmul fast float {p}t, {p}t",
            f"  {p}t3 = call float @dx.op.tertiary.f32(i32 46, float {p}t, float -2.000000e+00, float 3.000000e+00)",
            f"  {p}sm = fmul fast float {p}t2, {p}t3",
            f"  {p}sm1 = fadd fast float {p}sm, -1.000000e+00",
            f"  {p}a0 = call float @dx.op.tertiary.f32(i32 46, float %mod.cbs, float {p}sm1, float 1.000000e+00)",
            f"  {p}a = select i1 %mod.cbsoff, float 1.000000e+00, float {p}a0",
            f"  {p}amp = fmul fast float {p}a, {DETAIL_AMP}",
            ir.lines[j].replace(f"{x} = ", f"{p}xs = ", 1),
            f"  {p}xm = fmul fast float {site['A']}, {p}amp",
            f"  {x} = select i1 %mod.cbsoff, float {p}xs, float {p}xm",
        ]
        edits.append((j, lines))
        if i == 0:
            # the finer octave, view march only
            s2 = site["s2"]
            k = ir.defs[s2]
            c0, c1, c2 = site["coords"]
            q = "%mod.fo."
            edits.append((k, [
                ir.lines[k].replace(f"{s2} = ", f"{q}s2 = ", 1),
                f"  {q}u = fmul fast float {c0}, %mod.cff",
                f"  {q}v = fmul fast float {c1}, %mod.cff",
                f"  {q}w = fmul fast float {c2}, %mod.cff",
                f"  {q}lod = fadd fast float {site['lod']}, %mod.cfol",
                f"  {q}n = call %dx.types.ResRet.f32 @dx.op.sampleLevel.f32(i32 62, %dx.types.Handle {site['tex']}, "
                f"%dx.types.Handle {site['smp']}, float {q}u, float {q}v, float {q}w, float undef, i32 0, i32 0, "
                f"i32 0, float {q}lod)",
                f"  {q}nx = extractvalue %dx.types.ResRet.f32 {q}n, 0",
                f"  {q}d = fadd fast float {q}nx, -7.500000e-01",
                f"  {q}k = fmul fast float %mod.cfo, {p}a",
                f"  {q}s3 = call float @dx.op.tertiary.f32(i32 46, float {q}d, float {q}k, float {q}s2)",
                f"  {s2} = select i1 %mod.cfooff, float {q}s2, float {q}s3",
            ]))
    for j, new_lines in sorted(edits, key=lambda e: -e[0]):
        ir.lines[j:j + 1] = new_lines
    return f"cloud detail: {len(sites)} octave site(s), finer octave at {sites[0]['s2']}"


def apply_cloud_shape(ir: Ir) -> str:
    """ch_volumetric_clouds_render: build-up of bounced light (C6, C7) and
    crisper boundaries (C8), each exactly stock at 0.

    Powder: droplets scatter forward, so light heading back toward the sun
    has to bounce several times and builds up over the first metres inside a
    cloud. Seen with the sun behind, edges and thin parts are therefore darker
    than dense cores, which outlines each turret; MSFS instead brightens all
    edges alike. The sun phase is scaled by 1 - C6 v exp(-depth / C7), with
    depth the view sample's depth into the cloud (m), C7 the build-up depth
    (m, 100 if 0) and v = ((1 - cos) / 2)^2 from the sun-view angle, so it
    acts down-sun and fades out toward the sun (silver linings untouched).

    Edges: MSFS ramps density across every boundary. C8 steepens the ramp
    about its midpoint (the boundary stays put):
    sat((ramp - 0.5) * (1 + C8) + 0.5), in every march so shadows match.

    Glory (C9-C11): coloured rings around the antisolar point from droplets
    of radius r (C10, 10 um if 0). Per channel, 0.1 C9 * J0(z)^2 averaged over
    a size spread (C11, 0.15 if 0), as (1 + exp(-2 (spread z)^2) sin 2z) /
    (pi z + exp(-3 z)) (within 2% of the averaged Bessel pattern), with
    z = 2 pi r / lambda * gamma, gamma the angle from the antisolar point and
    lambda 680/550/440 nm. Added to the sun phase weighted by its
    single-scattering share s^2 (1 - C3), so it shows on sunlit cloud tops.
    Must run before apply_cloud_ms, which rewrites the phase's 1/4pi."""
    ms = find_cloud_ms(ir)
    edges = find_cloud_edges(ir)
    depth = edges["depth"]
    need(ir.defs[depth] < min(ir.defs[a] for a in ms["adds"]), f"{depth}: defined after the sun phase")
    channels = [0, 1, 2] if len(ms["adds"]) == 3 else [1]  # R, G, B; green alone for a grey phase
    # the sun-view cos = dot(view direction, sun direction from the Light cbuffer)
    need(ms["cos"] in ir.dot3, f"{ms['cos']}: the sun-view cos is not a dot3")
    ops = ir.dot3[ms["cos"]]
    def light_comp(v):
        if v in ir.extracts and ir.extracts[v][0] in ir.loads:
            handle, reg = ir.loads[ir.extracts[v][0]]
            if ir.cbs.get(handle, ("", 0))[1] == 176 and reg == 0:
                return ir.extracts[v][1]
        return None
    if [light_comp(v) for v in ops[3:]] == [0, 1, 2]:
        dirs, light = list(ops[:3]), list(ops[3:])
    else:
        need([light_comp(v) for v in ops[:3]] == [0, 1, 2], "no sun direction (Light c0) in the sun-view cos")
        dirs, light = list(ops[3:]), list(ops[:3])
    t = find_ap_depth(ir)  # the sample's distance along the view ray (m)
    need(ir.defs[t] < min(ir.defs[a] for a in ms["adds"]), f"{t}: defined after the sun phase")
    pix_em = Emitter("shp")
    pix = shadow_pixel_lines(pix_em, dirs, light)
    map_em = Emitter("shm")
    mp = shadow_map_pixel_lines(map_em, dirs)
    # the sun on the aircraft also through MSFS's cloud light volume (an overcast
    # above isn't in the cloud-layer map): the glory and the shadow need it
    lv_lines, lv, _ = light_volume_at_camera(ir, ms, ir.defs[ms["cos"]])
    lv_lines += [f"  %mod.lv.sat = call float @dx.op.unary.f32(i32 7, float {lv})",
                 f"  %mod.lv.vcam = fmul fast float {mp['vcam']}, %mod.lv.sat"]
    mp["vcam"] = "%mod.lv.vcam"
    edits = []
    # view factor, glory and shadow geometry, once, right after the sun-view cos
    i = ir.defs[ms["cos"]]
    edits.append((i, [
        ir.lines[i],
        f"  %mod.pw.v0 = call float @dx.op.tertiary.f32(i32 46, float {ms['cos']}, float -5.000000e-01, "
        f"float 5.000000e-01)",
        "  %mod.pw.v = fmul fast float %mod.pw.v0, %mod.pw.v0",
        "  %mod.pw.pv = fmul fast float %mod.pw.v, %mod.cpw",
        *map_em.lines,
        *lv_lines,
        f"  %mod.gl.k = fmul fast float %mod.cgl, {mp['vcam']}",
        *glory_lines(ms["cos"], channels, "%mod.gl.k"),
        *pix_em.lines,
    ]))
    sample_em = Emitter("shs")
    shadow = shadow_sample_lines(sample_em, pix, ms["cos"], dirs, light, t, ms["s"], mp)
    # per sample: f = 1 - p v exp(-max(depth, 0) / k)
    i = ir.defs[depth]
    edits.append((i, [
        ir.lines[i],
        f"  %mod.pw.d = call float @dx.op.binary.f32(i32 35, float {depth}, float 0.000000e+00)",
        "  %mod.pw.x = fmul fast float %mod.pw.d, %mod.cpk",
        "  %mod.pw.e = call float @dx.op.unary.f32(i32 21, float %mod.pw.x)",
        "  %mod.pw.npv = fsub fast float -0.000000e+00, %mod.pw.pv",
        "  %mod.pw.f = call float @dx.op.tertiary.f32(i32 46, float %mod.pw.npv, float %mod.pw.e, "
        "float 1.000000e+00)",
    ]))
    first = min(ir.defs[a] for a in ms["adds"])
    for k, (a, ch) in enumerate(zip(ms["adds"], channels)):
        j = ir.defs[a]
        edits.append((j, [
            # the shadow factor once per sample, before the first channel
            *(sample_em.lines + [f"  %mod.sh.f = select i1 {mp['off']}, float 1.000000e+00, float {shadow}"]
              if j == first else []),
            ir.lines[j].replace(f"{a} = ", f"%mod.pw.a{k} = ", 1),
            f"  %mod.pw.b{k} = fmul fast float %mod.pw.a{k}, %mod.pw.f",
            f"  %mod.pw.c{k} = select i1 %mod.cpwoff, float %mod.pw.a{k}, float %mod.pw.b{k}",
            f"  %mod.gl.t{k} = fmul fast float %mod.gl.p{ch}, {ms['s2']}",
            f"  %mod.gl.u{k} = fadd fast float %mod.pw.c{k}, %mod.gl.t{k}",
            f"  %mod.gl.v{k} = select i1 %mod.cgloff, float %mod.pw.c{k}, float %mod.gl.u{k}",
            f"  %mod.sh.m{k} = fmul fast float %mod.gl.v{k}, %mod.sh.f",
            f"  {a} = select i1 {mp['off']}, float %mod.gl.v{k}, float %mod.sh.m{k}",
        ]))
    for k, (x, ramp) in enumerate(edges["ramps"]):
        j = ir.defs[ramp]
        p = f"%mod.es{k}."
        edits.append((j, [
            ir.lines[j].replace(f"{ramp} = ", f"{p}r = ", 1),
            f"  {p}c = fsub fast float {p}r, 5.000000e-01",
            f"  {p}k = call float @dx.op.tertiary.f32(i32 46, float {p}c, float %mod.cek, float 5.000000e-01)",
            f"  {p}s = call float @dx.op.unary.f32(i32 7, float {p}k)",
            f"  {ramp} = select i1 %mod.ceoff, float {p}r, float {p}s",
        ]))
    for j, new_lines in sorted(edits, key=lambda e: -e[0]):
        ir.lines[j:j + 1] = new_lines
    return (f"cloud shape: powder, glory and aircraft shadow on {len(ms['adds'])} channel(s) (depth {depth}, "
            f"distance {t}), {len(edges['ramps'])} edge ramp(s)")


def find_ap_depth(ir: Ir) -> str:
    """The sample's distance (m) the cloud renderer looks the camera volume up
    at: the numerator of depth / Global[10].y (the AP range)."""
    glob = {h for h, (_, size) in ir.cbs.items() if size == 4800}
    ranges = {v for v, (load, comp) in ir.extracts.items()
              if comp == 1 and load in ir.loads and ir.loads[load][0] in glob and ir.loads[load][1] == 10}
    divs = [a for n, (k, a, b) in ir.binops.items() if k == "fdiv" and b in ranges]
    need(len(divs) == 1, f"{len(divs)} divisions by the AP range")
    return divs[0]


GLORY_WAVELENGTHS = (0.68, 0.55, 0.44)  # um, as AEROSOL_SPECTRAL_LOG2
# J0^2 + J2^2 (the glory of unpolarised light), averaged over droplet sizes:
# (1 + D (1 - exp(-m z^2)) sin(2z + phase)) / (pi z / 2 + exp(-k z)),
# D = exp(-2 (spread z)^2). Fitted jointly for spreads 0-0.25: within 0.045
# of the size-averaged Bessel pattern (centre 1, first ring 0.31-0.36 at z ~3.4).
GLORY_FIT_K, GLORY_FIT_M, GLORY_FIT_PHASE = 1.7613, 0.1597, 0.6384


def glory_lines(cos: str, channels, strength: str = "%mod.cgl") -> list[str]:
    """Per pixel: the glory's phase per channel, %mod.gl.p<c> (apply_cloud_shape),
    scaled by `strength` (0.1 C0 C9, times the sunlight at the camera)."""
    lines = [
        f"  %mod.gl.h0 = fadd fast float {cos}, 1.000000e+00",
        "  %mod.gl.h1 = call float @dx.op.binary.f32(i32 35, float %mod.gl.h0, float 0.000000e+00)",
        "  %mod.gl.h2 = fmul fast float %mod.gl.h1, 2.000000e+00",
        "  %mod.gl.g = call float @dx.op.unary.f32(i32 24, float %mod.gl.h2)",  # angle from the antisolar point
    ]
    for c in channels:
        q = f"%mod.gl.{c}"
        lines += [
            f"  {q}z = fmul fast float %mod.gl.g, %mod.cgx{c}",
            f"  {q}zz = call float @dx.op.tertiary.f32(i32 46, float {q}z, float 2.000000e+00, "
            f"float {f32(GLORY_FIT_PHASE)})",
            f"  {q}sn = call float @dx.op.unary.f32(i32 13, float {q}zz)",
            f"  {q}z2 = fmul fast float {q}z, {q}z",
            f"  {q}hx = fmul fast float {q}z2, {f32(-GLORY_FIT_M * LOG2E_F)}",
            f"  {q}he = call float @dx.op.unary.f32(i32 21, float {q}hx)",
            f"  {q}h = fsub fast float 1.000000e+00, {q}he",
            f"  {q}sz = fmul fast float {q}z, %mod.cgs",
            f"  {q}sq = fmul fast float {q}sz, {q}sz",
            f"  {q}dx = fmul fast float {q}sq, {f32(-2.0 * LOG2E_F)}",
            f"  {q}dd = call float @dx.op.unary.f32(i32 21, float {q}dx)",
            f"  {q}dh = fmul fast float {q}dd, {q}h",
            f"  {q}n = call float @dx.op.tertiary.f32(i32 46, float {q}dh, float {q}sn, float 1.000000e+00)",
            f"  {q}ex = fmul fast float {q}z, {f32(-GLORY_FIT_K * LOG2E_F)}",
            f"  {q}e3 = call float @dx.op.unary.f32(i32 21, float {q}ex)",
            f"  {q}den = call float @dx.op.tertiary.f32(i32 46, float {q}z, float {f32(math.pi / 2)}, float {q}e3)",
            f"  {q}G = fdiv fast float {q}n, {q}den",
            f"  %mod.gl.p{c} = fmul fast float {q}G, {strength}",
        ]
    return lines


# The aircraft's shadow (C12-C15). Silhouette in body axes (forward a, right b),
# as multiples of the length Lf = 0.95 * span (a) or the span s (b), with the
# eye (camera) 0.35 Lf ahead of the centre: (a0, b0, a1, b1, radius, radius unit).
SHADOW_LENGTH_PER_SPAN = 0.95
SHADOW_EYE_AHEAD = 0.35
SHADOW_CAPSULES = (
    ((0.5, 0.0), (-0.5, 0.0), 0.05, "L"),                       # fuselage
    ((0.05, 0.0), (0.05, -0.5), 0.045, "S", -0.125),            # left wing, tip swept back 0.125 s
    ((0.05, 0.0), (0.05, 0.5), 0.045, "S", -0.125),             # right wing
    ((-0.42, 0.0), (-0.42, -0.18), 0.03, "S", -0.06),           # left tailplane
    ((-0.42, 0.0), (-0.42, 0.18), 0.03, "S", -0.06),            # right tailplane
)
SUN_DIAMETER_TAN = 0.00925  # tan(0.53 deg): penumbra width per metre behind the aircraft
# Approximate cloud march step at distance t (m), for the shadow's smear within one step.
CLOUD_STEP_MIN_M, CLOUD_STEP_PER_M = 20.0, 0.008


def shadow_capsules(span: float):
    """The silhouette's capsules in body axes (m, eye at the origin):
    [((a0, b0), (a1, b1), radius)]. For the numpy reference in the tests."""
    lf = SHADOW_LENGTH_PER_SPAN * span
    out = []
    for cap in SHADOW_CAPSULES:
        (a0, b0), (a1, b1), r, unit = cap[:4]
        sweep = cap[4] if len(cap) > 4 else 0.0
        out.append((((a0 - SHADOW_EYE_AHEAD) * lf, b0 * span),
                    ((a1 - SHADOW_EYE_AHEAD) * lf + sweep * span, b1 * span),
                    r * (lf if unit == "L" else span)))
    return out


def shadow_pixel_lines(em: Emitter, dirs, light) -> dict:
    """Per pixel: the shadow plane (perpendicular to the sun), the silhouette's
    capsules projected into it and the view ray's offset per metre. The
    aircraft's forward axis is the sun's horizontal direction turned clockwise
    (seen from above) by C13 = heading - sun azimuth; the world is right-handed
    with y up, so from a horizontal v, v x up is 90 deg clockwise. Bank C14 (right wing down positive) tilts the right axis."""
    l0, l1, l2 = light
    hl = em.sqrt(em.max(em.mad(l2, l2, em.mul(l0, l0)), 1e-8))
    s0, s2 = em.div(l0, hl), em.div(l2, hl)                        # sun's horizontal direction
    f0 = em.sub(em.mul(s0, "%mod.cshcd"), em.mul(s2, "%mod.cshsd"))  # S cosD + (S x up) sinD
    f2 = em.mad(s0, "%mod.cshsd", em.mul(s2, "%mod.cshcd"))
    r0 = em.mul(em.sub(0.0, f2), "%mod.cshcb")                      # (F x up) cosB - up sinB
    r1 = em.sub(0.0, "%mod.cshsb")
    r2 = em.mul(f0, "%mod.cshcb")
    fl = em.mad(f2, l2, em.mul(f0, l0))
    p = [em.sub(f0, em.mul(fl, l0)), em.sub(0.0, em.mul(fl, l1)), em.sub(f2, em.mul(fl, l2))]
    pn = em.div(1.0, em.sqrt(em.max(em.mad(p[2], p[2], em.mad(p[1], p[1], em.mul(p[0], p[0]))), 1e-8)))
    e1 = [em.mul(x, pn) for x in p]
    e2 = [em.sub(em.mul(l1, e1[2]), em.mul(l2, e1[1])),
          em.sub(em.mul(l2, e1[0]), em.mul(l0, e1[2])),
          em.sub(em.mul(l0, e1[1]), em.mul(l1, e1[0]))]

    def dot(a, b):
        return em.mad(a[2], b[2], em.mad(a[1], b[1], em.mul(a[0], b[0])))
    right = [r0, r1, r2]
    F2 = (em.mad(f2, e1[2], em.mul(f0, e1[0])), em.mad(f2, e2[2], em.mul(f0, e2[0])))
    R2 = (dot(right, e1), dot(right, e2))
    caps = []
    for (a0, b0), (a1, b1), r, unit, *sweep in SHADOW_CAPSULES:
        def point(a, b, sw=0.0):
            # (a - eye) Lf + sw span along, b span across, in body axes -> plane
            along = em.mul("%mod.cshlf", a - SHADOW_EYE_AHEAD)
            if sw:
                along = em.mad("%mod.cshspan", sw, along)
            side = em.mul("%mod.cshspan", b)
            return (em.mad(along, F2[0], em.mul(side, R2[0])), em.mad(along, F2[1], em.mul(side, R2[1])))
        A = point(a0, b0)
        B = point(a1, b1, sweep[0] if sweep else 0.0)
        D = (em.sub(B[0], A[0]), em.sub(B[1], A[1]))
        inv = em.div(1.0, em.max(em.mad(D[1], D[1], em.mul(D[0], D[0])), 1e-6))
        radius = em.mul("%mod.cshlf" if unit == "L" else "%mod.cshspan", r)
        caps.append((A, D, inv, radius))
    return {"e1": e1, "e2": e2, "caps": caps}


SIL_LOOKUP_LEVELS = 5  # the cloud lookup's coarsest level (level 6 is mostly its zero ring)
SIL_FRESH_FRAMES = 2   # the window counts as current up to this many frames old


def light_volume_at_camera(ir: Ir, ms: dict, before: int) -> tuple[list[str], str]:
    """The sun's visibility V at the camera from MSFS's cloud light volume,
    for the glory and the aircraft's shadow. V's chain is copied with the
    march distance set to 0 and the position at the march's start (the
    camera); it is found from the soft transmittance that feeds the sun
    visibility s. Returns (lines, V, substitutions); every value reused from
    the shader must be defined before line `before` (the per-pixel block)."""
    # the soft transmittance that feeds s (through the phi after its branch)
    reach, todo = set(), [ms["s"]]
    while todo:
        v = todo.pop()
        if v in reach or not re.fullmatch(r"%\d+", v) or v not in ir.defs:
            continue
        reach.add(v)
        rhs = re.sub(r"\s+;.*$", "", ir.lines[ir.defs[v]].split(" = ", 1)[1])
        todo += re.findall(r"%\d+\b", rhs)
    xs = [x for x, _ in ms["softs"] if x in reach]
    need(len(xs) == 1, f"{len(xs)} soft transmittances feed the sun visibility")
    x = xs[0]
    need(x in ir.binops and ir.binops[x][0] == "fdiv", f"{x}: d is not (1 - V) / V")
    num, vis = ir.binops[x][1:]
    need(num in ir.binops and ir.binops[num][:2] == ("fsub", "1.000000e+00") and ir.binops[num][2] == vis,
         f"{x}: d is not (1 - V) / V")

    # the march's loop phis in V's chain: the three position components and the distance
    phi_re = re.compile(r"\s+(%\d+) = phi float (.*)$")
    loop, outer, seen, todo = {}, set(), set(), [vis]
    while todo:
        v = todo.pop()
        if v in seen or not re.fullmatch(r"%\d+", v) or v not in ir.defs:
            continue
        seen.add(v)
        line = re.sub(r"\s+;.*$", "", ir.lines[ir.defs[v]])
        if m := phi_re.match(line):
            incoming = re.findall(r"\[ (\S+), (%\d+) \]", m[2])
            inits = [val for val, _ in incoming if re.fullmatch(r"%\d+", val) and ir.defs[val] < ir.defs[v]]
            later = [val for val, _ in incoming if re.fullmatch(r"%\d+", val) and ir.defs[val] > ir.defs[v]]
            if later:
                need(len(inits) == 1, f"{v}: a loop phi without one initial value")
                loop[v] = inits[0]
            else:
                outer.add(v)
            continue
        todo += re.findall(r"%\d+\b", line.split(" = ", 1)[1])
    subst, starts = {}, {}
    for phi, init in loop.items():
        if init in ir.binops and ir.binops[init][0] == "fadd":
            for a, b in (ir.binops[init][1:], ir.binops[init][2:0:-1]):
                if a in ir.binops and ir.binops[a][0] == "fmul":
                    subst[phi] = b               # position component: its base, the camera
                    starts[phi] = ir.binops[a][1:]
                    break
    t0s = {op for ops in starts.values() for op in ops}
    for phi, init in loop.items():
        if phi not in subst:
            need(init in t0s, f"{phi}: a march phi that is neither the position nor the distance")
            subst[phi] = "0.000000e+00"         # the distance along the ray
    need(sum(1 for p in loop if subst[p] != "0.000000e+00") == 3, "no 3-component march position")
    for phi in outer:
        subst[phi] = phi
    for keep in [v for v in subst.values() if re.fullmatch(r"%\d+", v)]:
        need(ir.defs[keep] < before, f"{keep}: defined after the per-pixel block")
    lines, memo = [], {}
    value = clone_chain(ir, vis, subst, "lv.", lines, memo)
    return lines, value, subst


def shadow_map_pixel_lines(em: Emitter, dirs) -> dict:
    """Per pixel: the live window's header (SIL_REGION), read through the
    debug buffer (u0 space50) with DebugCapture bit 4 set; ok when its flag
    was written within the last SIL_FRESH_FRAMES frames (the aircraft is in
    the sun's shadow map: Raytraced Shadows off), else the capsules stand in.
    The view ray's step through the window per metre (du, dv; level-0 cells)."""
    uh = em.new("call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
                f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)")
    u = em.new(f"call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle {uh}, "
               "%dx.types.ResourceProperties { i32 4107, i32 0 })")
    r0 = em.new("call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.ah, i32 0)")
    f0 = em.new(f"extractvalue %dx.types.CBufRet.f32 {r0}, 0")
    flags = em.new(f"bitcast float {f0} to i32")
    on = em.new(f"icmp ne i32 {em.new(f'and i32 {flags}, 16')}, 0")
    frame = em.new(f"lshr i32 {flags}, 16")
    base = em.new(f"select i1 {on}, i32 {SIL_REGION}, i32 0")

    def load(off):
        addr = em.new(f"add i32 {base}, {off}")
        return em.new(f"call %dx.types.ResRet.f32 @dx.op.rawBufferLoad.f32(i32 139, %dx.types.Handle {u}, "
                      f"i32 {addr}, i32 undef, i8 15, i32 4)")
    h0, h1, h2, h5 = load(0), load(16), load(32), load(80)

    def comp(h, k):
        return em.new(f"extractvalue %dx.types.ResRet.f32 {h}, {k}")
    flag = em.new(f"bitcast float {comp(h0, 0)} to i32")
    c0 = comp(h0, 1)
    fbit = em.new(f"and i32 {flag}, 65536")
    fset = em.new(f"icmp ne i32 {fbit}, 0")
    written = em.new(f"and i32 {flag}, 65535")
    age = em.new(f"and i32 {em.new(f'sub i32 {frame}, {written}')}, 65535")
    fresh = em.new(f"icmp ule i32 {age}, {SIL_FRESH_FRAMES}")
    usable = em.new(f"and i1 {fresh}, {em.cmp('ogt', c0, 0.0)}")
    ok = em.new(f"and i1 {em.new(f'and i1 {on}, {fset}')}, {usable}")
    U = [comp(h1, k) for k in range(4)]
    V = [comp(h2, k) for k in range(4)]
    du = em.mad(U[2], dirs[2], em.mad(U[1], dirs[1], em.mul(U[0], dirs[0])))
    dv = em.mad(V[2], dirs[2], em.mad(V[1], dirs[1], em.mul(V[0], dirs[0])))
    ic0 = em.div(1.0, em.max(c0, 1e-6))
    # the direct sunlight at the camera (MSFS's cloud-layer shadow), from a
    # header written within the last frames, else 1: the glory and the
    # aircraft's shadow both need the sun on the aircraft
    hframe = em.new(f"bitcast float {comp(h5, 1)} to i32")
    hset = em.new(f"icmp ne i32 {em.new(f'and i32 {hframe}, 65536')}, 0")
    hwritten = em.new(f"and i32 {hframe}, 65535")
    hage = em.new(f"and i32 {em.new(f'sub i32 {frame}, {hwritten}')}, 65535")
    hfresh = em.new(f"and i1 {em.new(f'and i1 {on}, {hset}')}, {em.new(f'icmp ule i32 {hage}, {SIL_FRESH_FRAMES}')}")
    vcam = em.select(hfresh, em.sat(comp(h5, 0)), 1.0)
    # the shadow is on with C12 > 0 and either the capsules (cockpit, span) or the window
    stale = em.new(f"xor i1 {ok}, true")
    off = em.new(f"or i1 %mod.cshoff0, {em.new(f'and i1 %mod.cshoff1, {stale}')}")
    return {"u": u, "base": base, "ok": ok, "du": du, "dv": dv, "x0": U[3], "y0": V[3], "ic0": ic0, "off": off,
            "vcam": vcam}


def shadow_map_sample_lines(em: Emitter, mp: dict, t: str, ipw: str) -> str:
    """Per sample at distance t: the window's coverage at the sample's spot,
    box-filtered to the penumbra width 1 / ipw: trilinear between the two
    pyramid levels around it (each bilinear, clamped into the window, whose
    outer ring is 0), dimmed by (cell / pw)^2 beyond the coarsest level."""
    x = em.mad(t, mp["du"], mp["x0"])
    y = em.mad(t, mp["dv"], mp["y0"])
    pwc = em.div(mp["ic0"], ipw)                      # penumbra width in level-0 cells
    lmax = float(SIL_LOOKUP_LEVELS)
    lf = em.min(em.max(em.unary(23, em.max(pwc, 1e-3)), 0.0), lmax)
    l0 = em.unary(27, lf)
    wl = em.sub(lf, l0)
    l1 = em.min(em.add(l0, 1.0), lmax)

    def bilinear(lv):
        sc = em.unary(21, em.sub(0.0, lv))
        nm1 = em.sub(em.mul(sc, float(SIL_N0)), 1.0)
        n = em.add(nm1, 1.0)
        xl, yl = em.mad(x, sc, -0.5), em.mad(y, sc, -0.5)
        fx, fy = em.unary(27, xl), em.unary(27, yl)
        ax, ay = em.sub(xl, fx), em.sub(yl, fy)
        off = float(SIL_OFFSETS[0])
        for L in range(1, SIL_LOOKUP_LEVELS + 1):
            off = em.select(em.cmp("oeq", lv, float(L)), float(SIL_OFFSETS[L]), off)
        vals = []
        for dy in (0.0, 1.0):
            for dx in (0.0, 1.0):
                cx = em.min(em.max(em.add(fx, dx), 0.0), nm1)
                cy = em.min(em.max(em.add(fy, dy), 0.0), nm1)
                byte = em.mad(em.mad(cy, n, cx), 4.0, off)
                addr = em.new(f"add i32 {mp['base']}, {em.new(f'fptosi float {byte} to i32')}")
                ld = em.new(f"call %dx.types.ResRet.f32 @dx.op.rawBufferLoad.f32(i32 139, %dx.types.Handle {mp['u']}, "
                            f"i32 {addr}, i32 undef, i8 1, i32 4)")
                vals.append(em.new(f"extractvalue %dx.types.ResRet.f32 {ld}, 0"))
        top = em.mad(em.sub(vals[1], vals[0]), ax, vals[0])
        bottom = em.mad(em.sub(vals[3], vals[2]), ax, vals[2])
        return em.mad(em.sub(bottom, top), ay, top)
    c0v, c1v = bilinear(l0), bilinear(l1)
    cov = em.mad(em.sub(c1v, c0v), wl, c0v)
    big = em.max(em.mul(pwc, 1.0 / 2 ** SIL_LOOKUP_LEVELS), 1.0)
    return em.div(cov, em.mul(big, big))


def shadow_sample_lines(em: Emitter, pix: dict, cos: str, dirs, light, t: str, s: str, mp: dict = None) -> str:
    """Per sample at distance t (m) along the view ray: the sun term's factor
    1 - C12 * occlusion * s^2. The sample is t * dir from the camera (the
    aircraft); its offset from the shadow axis is t * (dir - cos L), its
    distance behind the aircraft along the sunlight -t cos. Occlusion per
    capsule sat((r - d) / pw + 0.5) * sat(2.5 r / pw), the max over the
    capsules, with pw the larger of
      - the penumbra, 0.00925 * behind (the sun's 0.53 deg), and
      - how far the footprint moves within one march step, about
        (CLOUD_STEP_MIN_M + CLOUD_STEP_PER_M t) * sin(angle from the antisolar
        point): sub-metre on
        the glory, but near the camera inside a cloud it merges the steps'
        separate silhouettes into one soft shadow tunnel.
    s^2 weights the shadow by direct sunlight (s = the sample's saturated sun
    visibility): 1 at a sunlit cloud surface, where the beam the aircraft blocks is what
    lights the cloud, and small inside one, where scattered light fills the
    shadow in."""
    w = [em.sub(dirs[k], em.mul(cos, light[k])) for k in range(3)]
    e1, e2 = pix["e1"], pix["e2"]
    w1 = em.mad(w[2], e1[2], em.mad(w[1], e1[1], em.mul(w[0], e1[0])))
    w2 = em.mad(w[2], e2[2], em.mad(w[1], e2[1], em.mul(w[0], e2[0])))
    q1, q2 = em.mul(t, w1), em.mul(t, w2)
    behind = em.mul(em.sub(0.0, t), cos)
    sin_off = em.sqrt(em.max(em.sub(1.0, em.mul(cos, cos)), 0.0))
    smear = em.mul(em.mad(t, CLOUD_STEP_PER_M, CLOUD_STEP_MIN_M), sin_off)
    ipw = em.div(1.0, em.max(em.max(em.mul(behind, SUN_DIAMETER_TAN), smear), 0.05))
    occ = None
    for A, D, inv, radius in pix["caps"]:
        dx, dy = em.sub(q1, A[0]), em.sub(q2, A[1])
        h = em.sat(em.mul(em.mad(dy, D[1], em.mul(dx, D[0])), inv))
        ex, ey = em.sub(dx, em.mul(h, D[0])), em.sub(dy, em.mul(h, D[1]))
        d = em.sqrt(em.mad(ey, ey, em.mul(ex, ex)))
        o = em.mul(em.sat(em.mad(em.sub(radius, d), ipw, 0.5)), em.sat(em.mul(em.mul(radius, ipw), 2.5)))
        occ = o if occ is None else em.max(occ, o)
    if mp is not None:
        # the aircraft's real outline from the sun's shadow map, when this frame's window holds it
        occ = em.select(mp["ok"], shadow_map_sample_lines(em, mp, t, ipw), occ)
    occ = em.mul(em.select(em.cmp("ogt", behind, 0.0), occ, 0.0), em.mul(s, s))
    if mp is not None:
        occ = em.mul(occ, mp["vcam"])
    return em.new(f"call float @dx.op.tertiary.f32(i32 46, float {em.sub(0.0, occ)}, float %mod.cshs, "
                  f"float 1.000000e+00)")


def patch_clouds(text: str) -> tuple[str, str]:
    """ch_volumetric_clouds_render: the haze colour (T11), the build-up of
    bounced light, crisper edges and the glory (C6-C11), multiple scattering
    (C3-C5) and the forward lobe (C0-C2). The order matters: each step finds the stock
    patterns the next one rewrites."""
    ir = Ir(text)
    colour = apply_cloud_ap(ir)
    ir.reindex()
    optical = apply_cloud_optical(ir)  # (before apply_cloud_shape renames the edge ramps)
    ir.reindex()
    detail = apply_cloud_detail(ir)
    ir.reindex()
    shape = apply_cloud_shape(ir)
    ir.reindex()
    ms = apply_cloud_ms(ir)
    ir.reindex()
    phase = apply_cloud_phase(ir)
    user = append_user_dump(ir)
    return finish(ir, cloud=True, debug_uav=True), f"{colour}; {optical}; {detail}; {shape}; {ms}; {phase}; {user}"


DEBUG_REGION_SHADOW = 2048       # ch_deferred_shadows: its 720-byte shadow cbuffer, 43 registers
DEBUG_REGION_SHADOW_GRID = 4096  # ... and 4 cascades x 16 x 16 shadow-map depths around the camera
SHADOW_GRID = 16
SHADOW_GRID_STEP_M = 6.0
SHADOW_CELL_TAPS = 8  # each grid cell: the max depth of 8 x 8 loads across it
# With DebugCapture bit 3 (the add-on's 2 MB debug buffer): every cascade whole
# as a 192 x 192 image (per pixel the max and the smallest non-empty depth of
# 8 x 8 loads across it: every texel of a 1536^2 map), and this pass's output,
# the screen's sun visibility, at every 8th pixel in x and y.
DEBUG_REGION_SHADOW_IMAGE = 0x10000
SHADOW_IMAGE = 192
DEBUG_REGION_SHADOW_MASK = 0x140000
SHADOW_MASK_STEP = 8
SHADOW_MASK_STRIDE = 512  # up to 4096 x 2560 pixels
SHADOW_MASK_ROWS = 320
DEBUG_MAGIC = 0x4F4D5441         # 'ATMO', as kDebugMagic in mod_common.hlsli


# The aircraft's live shadow: every frame, with
# DebugCapture bit 4 (the add-on's 4 MB buffer), ch_deferred_shadows copies a
# window of the sun's shadow map around the camera into the debug buffer, and
# ch_volumetric_clouds_render projects its samples into it. Layout (bytes from
# SIL_REGION):
#   0   i32 flag: (frame & 0xFFFF) | 0x10000, written by any level-0 cell with
#       a caster this frame (frame = DebugCapture >> 16)
#   4   f32 c0 (level-0 cell, m), dpm (depth per m), the camera's depth
#   16  float4 U, V: camera-relative world position -> level-0 cell (x, y)
#   48  float4 D: -> shadow-map depth (larger = toward the sun)
#   64  i32 cascade, f32 window W (m), f32 A (m), i32 frame
#   80  f32 direct sunlight at the camera through clouds (MSFS's cloud-layer
#       shadow, cloud_shadow_at_camera; 1 without it), i32 the header's frame
#       ((frame & 0xFFFF) | 0x10000, written every frame by thread 0)
#   96  coverage pyramid: level L is (256 >> L)^2 floats, L = 0..6, the
#       fraction of a cell's texels holding a caster within A + 20 m (along
#       the light) of the camera; each level's outer ring is 0, so lookups
#       clamped into the window read 0 outside it.
# The window is W = 2.4 x span (24-200 m) in the cockpit view (C15 = span),
# else 160 m (C15 <= 0: span unknown or an external view), in the finest
# cascade that holds A (W / 2, else 60 m) around the camera.
SIL_REGION = 0x380000
# On capture with bit 4: the whole Global cbuffer (300 registers) from ch_deferred_shadows.
DEBUG_REGION_GLOBAL = 0x3E0000
GLOBAL_REGS = 300
SIL_HEADER = 96
SIL_LEVELS = 7
SIL_N0 = 256
SIL_OFFSETS = [SIL_HEADER + 4 * sum((SIL_N0 >> l) ** 2 for l in range(L)) for L in range(SIL_LEVELS + 1)]
SIL_SPAN_FACTOR, SIL_MIN_M, SIL_MAX_M = 2.4, 24.0, 200.0
SIL_EXTERNAL_M, SIL_EXTERNAL_A_M, SIL_BAND_MARGIN_M = 160.0, 60.0, 20.0
SIL_TAPS0, SIL_TAPS = 4, 8  # per cell and axis: level 0, levels 1-6
SIL_RAYS0, SIL_RAYS = 2, 4  # ... rays with the TLAS bound (DebugCapture bit 5)
# FORCE_OPAQUE | ACCEPT_FIRST_HIT_AND_END_SEARCH | SKIP_PROCEDURAL_PRIMITIVES: one
# Proceed settles the query
RAY_QUERY_FLAGS = 517
# instance mask 1: the shadow casters only, so reflection-only instances stay out
# of the cloud shadow
RAY_INSTANCE_MASK = 1


def clone_chain(ir: Ir, root: str, subst: dict, prefix: str, lines: list, memo: dict) -> str:
    """Re-emit the instructions that compute `root` (dxc's numbered values,
    followed through their operands) with the values in `subst` replaced,
    named %mod.<prefix><n>, appending them to `lines` in dependency order.
    Binding handles are reused; phis are refused (the chain must be
    straight-line). Returns the name standing for root."""
    if root in subst:
        return subst[root]
    if not re.fullmatch(r"%\d+", root):
        return root
    if root in memo:
        return memo[root]
    rhs = re.sub(r"\s+;.*$", "", ir.lines[ir.defs[root]].split(" = ", 1)[1])
    need(not rhs.startswith("phi "), f"{root}: a phi in the chain")
    if re.search(r"@dx\.op\.(createHandleFromBinding|threadId|groupId|threadIdInGroup|flattenedThreadIdInGroup)\b",
                 rhs):
        memo[root] = root  # binding handles and system values (defined at entry): reused
        return root
    new_rhs = re.sub(r"%\d+\b", lambda m: clone_chain(ir, m[0], subst, prefix, lines, memo), rhs)
    name = f"%mod.{prefix}{root[1:]}"
    lines.append(f"  {name} = {new_rhs}")
    memo[root] = name
    return name


def cloud_shadow_at_camera(ir: Ir, visibility: str, glob: str) -> tuple[list[str], str]:
    """ch_deferred_shadows' cloud-layer shadow, evaluated at the camera. The
    stock shader applies it to the stored visibility on one side of a branch;
    we copy that factor and the branch condition with the view-space position
    set to 0. Returns (lines, value): the direct sunlight reaching the
    aircraft through clouds, 0..1; ([], 1.0) in variants without the lookup."""
    m = re.match(r"\s+%\S+ = phi float \[ (%\d+), (%\d+) \], \[ (%\d+), (%\d+) \]", ir.lines[ir.defs[visibility]])
    if not m:
        return [], "1.000000e+00"
    a, blk_a, b, blk_b = m[1], m[2], m[3], m[4]
    if a in ir.binops and ir.binops[a][0] == "fmul" and b in ir.binops[a][1:]:
        factor, block = [x for x in ir.binops[a][1:] if x != b][0], blk_a
    elif b in ir.binops and ir.binops[b][0] == "fmul" and a in ir.binops[b][1:]:
        factor, block = [x for x in ir.binops[b][1:] if x != a][0], blk_b
    else:
        return [], "1.000000e+00"
    br = [re.match(r"\s+br i1 (%\d+), label (%\d+), label (%\d+)", l) for l in ir.lines]
    conds = [x for x in br if x and block in (x[2], x[3])]
    need(len(conds) == 1, f"{len(conds)} branches into the cloud shadow's block {block}")
    cond, taken = conds[0][1], conds[0][2] == block

    # the three view-space position components (offset from the camera by Global[134..136].w)
    position = set()
    seen = set()

    def walk(v):
        if v in seen or not re.fullmatch(r"%\d+", v):
            return
        seen.add(v)
        if v in ir.binops:
            kind, x, y = ir.binops[v]
            if kind == "fsub" and y in ir.extracts and ir.extracts[y][1] == 3:
                load = ir.extracts[y][0]
                ld = re.search(r"cbufferLoadLegacy\.f32\(i32 59, %dx\.types\.Handle (%\d+), i32 (\S+)\)",
                               ir.lines[ir.defs[load]]) if load in ir.defs else None
                if ld and ld[1] == glob:
                    position.add(x)
                    return
        rhs = ir.lines[ir.defs[v]].split(" = ", 1)[1] if v in ir.defs else ""
        for op in re.findall(r"%\d+\b", re.sub(r"\s+;.*$", "", rhs)):
            walk(op)
    walk(factor)
    walk(cond)
    need(len(position) == 3, f"{len(position)} view-position components in the cloud shadow")
    lines, memo = [], {}
    subst = {p: "0.000000e+00" for p in position}
    c = clone_chain(ir, factor, subst, "vc.", lines, memo)
    k = clone_chain(ir, cond, subst, "vc.", lines, memo)
    on, off = (c, "1.000000e+00") if taken else ("1.000000e+00", c)
    lines.append(f"  %mod.vc.v = select i1 {k}, float {on}, float {off}")
    return lines, "%mod.vc.v"


def silhouette_lines(cb: str, glob: str, map_handle: str, map_props: str, tid: dict, end: str,
                     camera_sun: tuple = ([], "1.000000e+00")) -> list[str]:
    """ch_deferred_shadows' blocks mod.sl.* (see SIL_REGION), entered at
    mod.sl.0 and leaving to `end`. Threads (x < 256, y < 256) build level 0,
    threads (x in 256..507) the coarser levels: level L at x from
    512 - 2 (256 >> L), y < 256 >> L. Needs %mod.sc.flags, %mod.sc.u,
    %mod.sc.xy0 and the cloud prologue (%mod.C15)."""
    t = "%dx.types.CBufRet.f32"
    x, y = tid[0], tid[1]
    out = [
        "mod.sl.0:",
        "  %mod.sl.bit16 = and i32 %mod.sc.flags, 16",
        "  %mod.sl.on = icmp ne i32 %mod.sl.bit16, 0",
        "  %mod.sl.f16 = lshr i32 %mod.sc.flags, 16",
        "  %mod.sl.fv = or i32 %mod.sl.f16, 65536",
        f"  %mod.sl.x0 = icmp ult i32 {x}, {SIL_N0}",
        f"  %mod.sl.y0 = icmp ult i32 {y}, {SIL_N0}",
        "  %mod.sl.is0 = and i1 %mod.sl.x0, %mod.sl.y0",
        f"  %mod.sl.xn0 = icmp uge i32 {x}, {SIL_N0}",
        f"  %mod.sl.xn1 = icmp ult i32 {x}, {2 * SIL_N0 - (SIL_N0 >> (SIL_LEVELS - 1))}",
        "  %mod.sl.xn = and i1 %mod.sl.xn0, %mod.sl.xn1",
    ]
    # level L for x >= 256: 1 + [x >= 384] + [x >= 448] + ...
    level = "1"
    for L in range(2, SIL_LEVELS):
        start = 2 * SIL_N0 - 2 * (SIL_N0 >> L)
        out += [f"  %mod.sl.ge{L} = icmp uge i32 {x}, {start}",
                f"  %mod.sl.gz{L} = zext i1 %mod.sl.ge{L} to i32",
                f"  %mod.sl.lv{L} = add i32 {level}, %mod.sl.gz{L}"]
        level = f"%mod.sl.lv{L}"
    out += [
        f"  %mod.sl.L = add i32 {level}, 0",
        f"  %mod.sl.NL = lshr i32 {SIL_N0}, %mod.sl.L",
        "  %mod.sl.NL2 = shl i32 %mod.sl.NL, 1",
        f"  %mod.sl.st = sub i32 {2 * SIL_N0}, %mod.sl.NL2",
        f"  %mod.sl.i = sub i32 {x}, %mod.sl.st",
        f"  %mod.sl.yn = icmp ult i32 {y}, %mod.sl.NL",
        "  %mod.sl.isn = and i1 %mod.sl.xn, %mod.sl.yn",
        "  %mod.sl.role = or i1 %mod.sl.is0, %mod.sl.isn",
        "  %mod.sl.go = and i1 %mod.sl.on, %mod.sl.role",
        "  br i1 %mod.sl.go, label %mod.sl.setup, label " + end,
        "",
        "mod.sl.setup:",
    ]
    em = Emitter("sl.e")
    span = em.unary(6, "%mod.C15")
    cockpit = em.cmp("ogt", "%mod.C15", 0.0)
    w = em.select(cockpit, em.min(em.max(em.mul(span, SIL_SPAN_FACTOR), SIL_MIN_M), SIL_MAX_M), SIL_EXTERNAL_M)
    a = em.select(cockpit, em.mul(w, 0.5), SIL_EXTERNAL_A_M)
    out += em.lines
    em.lines = []
    rows = {}
    for r in list(range(20)) + [40, 42]:
        out.append(f"  %mod.sl.c{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {cb}, i32 {r})")
        for c in range(4):
            out.append(f"  %mod.sl.c{r}.{c} = extractvalue {t} %mod.sl.c{r}, {c}")
        rows[r] = [f"%mod.sl.c{r}.{c}" for c in range(4)]
    out += ["  %mod.sl.cnt = bitcast float %mod.sl.c42.0 to i32"]
    fits = []
    for k in range(4):
        u, v = rows[5 * k], rows[5 * k + 1]
        upm = em.sqrt(em.mad(u[2], u[2], em.mad(u[1], u[1], em.mul(u[0], u[0]))))
        vpm = em.sqrt(em.mad(v[2], v[2], em.mad(v[1], v[1], em.mul(v[0], v[0]))))
        au, av = em.mul(a, upm), em.mul(a, vpm)
        conds = [em.cmp("oge", em.sub(u[3], au), 0.0), em.cmp("ole", em.add(u[3], au), 1.0),
                 em.cmp("oge", em.sub(v[3], av), 0.0), em.cmp("ole", em.add(v[3], av), 1.0),
                 em.cmp("ogt", upm, 0.0)]
        out += em.lines
        em.lines = []
        out.append(f"  %mod.sl.kc{k} = icmp sgt i32 %mod.sl.cnt, {k}")
        f = f"%mod.sl.kc{k}"
        for n, c in enumerate(conds):
            out.append(f"  %mod.sl.fit{k}_{n} = and i1 {f}, {c}")
            f = f"%mod.sl.fit{k}_{n}"
        fits.append(f)
    # the finest cascade that fits (else the last)
    out.append("  %mod.sl.k3 = add i32 3, 0")
    kk = "%mod.sl.k3"
    for k in (2, 1, 0):
        out.append(f"  %mod.sl.k{k} = select i1 {fits[k]}, i32 {k}, i32 {kk}")
        kk = f"%mod.sl.k{k}"
    sel = {}
    for r in range(3):
        for c in range(4):
            v = rows[15 + r][c]
            for k in (2, 1, 0):
                out.append(f"  %mod.sl.m{r}{c}_{k} = select i1 {fits[k]}, float {rows[5 * k + r][c]}, float {v}")
                v = f"%mod.sl.m{r}{c}_{k}"
            sel[r, c] = v
    # (the select chain picks the finest fitting k because later k's are overwritten by earlier ones)
    u = [sel[0, c] for c in range(4)]
    v = [sel[1, c] for c in range(4)]
    d = [sel[2, c] for c in range(4)]
    size = rows[40][0]
    upm = em.sqrt(em.mad(u[2], u[2], em.mad(u[1], u[1], em.mul(u[0], u[0]))))
    vpm = em.sqrt(em.mad(v[2], v[2], em.mad(v[1], v[1], em.mul(v[0], v[0]))))
    dpm = em.sqrt(em.mad(d[2], d[2], em.mad(d[1], d[1], em.mul(d[0], d[0]))))
    c0 = em.mul(w, 1.0 / SIL_N0)
    half = em.mul(w, 0.5)
    cu = em.mul(em.sub(u[3], em.mul(half, upm)), size)   # the window's corner, texels
    cv = em.mul(em.sub(v[3], em.mul(half, vpm)), size)
    tu = em.mul(em.mul(c0, upm), size)                    # texels per level-0 cell
    tv = em.mul(em.mul(c0, vpm), size)
    band = em.mul(em.add(a, SIL_BAND_MARGIN_M), dpm)
    hi = em.sub(size, 1.0)
    out += em.lines
    em.lines = []
    # With DebugCapture bit 5 the add-on has bound MSFS's ray-tracing scene (its
    # TLAS, t0 space50): Raytraced Shadows leave the aircraft out of the
    # cascades, so the cells also trace short rays along the light through the
    # band (camera-relative world: the view rows times Global[134..136]).
    out += ["  %mod.sl.bit32 = and i32 %mod.sc.flags, 32",
            "  %mod.sl.rt = icmp ne i32 %mod.sl.bit32, 0"]
    for r in range(3):
        out.append(f"  %mod.sl.r{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {glob}, i32 {134 + r})")
        out += [f"  %mod.sl.r{r}.{c} = extractvalue {t} %mod.sl.r{r}, {c}" for c in range(3)]

    def to_world(row, scale):
        return [em.mul(em.mad(row[2], f"%mod.sl.r2.{c}", em.mad(row[1], f"%mod.sl.r1.{c}",
                                                                em.mul(row[0], f"%mod.sl.r0.{c}"))), scale)
                for c in range(3)]
    e_u = to_world(u, em.div(1.0, upm))   # unit axes of the light plane, world
    e_v = to_world(v, em.div(1.0, vpm))
    lsun = to_world(d, em.div(1.0, dpm))  # toward the sun
    band_m = em.add(a, SIL_BAND_MARGIN_M)
    ray_len = em.mul(band_m, 2.0)
    out += em.lines
    em.lines = []

    def with_rays(lines: list, cov: str, tag: str, block: str, ci: str, cj: str, scale: str, taps: int):
        """Adds to a coverage block (label `block`, coverage `cov`): with the
        TLAS bound, taps x taps rays per cell along the light through the band;
        the cell's coverage is the larger of the map's and the rays'."""
        lines = lines + [f"  br i1 %mod.sl.rt, label %mod.sl.{tag}r, label %mod.sl.{tag}m", "", f"mod.sl.{tag}r:",
                         "  %mod.sl.{0}h = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, "
                         "%dx.types.ResBind {{ i32 0, i32 0, i32 {1}, i8 0 }}, i32 0, i1 false)".format(tag, MOD_SPACE),
                         f"  %mod.sl.{tag}a = call %dx.types.Handle @dx.op.annotateHandle(i32 216, "
                         f"%dx.types.Handle %mod.sl.{tag}h, %dx.types.ResourceProperties {{ i32 16, i32 0 }})",
                         f"  %mod.sl.{tag}q = call i32 @dx.op.allocateRayQuery(i32 178, i32 {RAY_QUERY_FLAGS})"]
        e = Emitter(f"sl.{tag}y")
        fi, fj = e.new(f"uitofp i32 {ci} to float"), e.new(f"uitofp i32 {cj} to float")
        lines += e.lines
        total = "0.000000e+00"
        for p in range(taps):
            for q in range(taps):
                e2 = Emitter(f"sl.{tag}y{p}_{q}.")
                xa = e2.mul(e2.sub(e2.mul(e2.add(fi, (p + 0.5) / taps), scale), SIL_N0 / 2), c0)
                yb = e2.mul(e2.sub(e2.mul(e2.add(fj, (q + 0.5) / taps), scale), SIL_N0 / 2), c0)
                o = [e2.mad(xa, e_u[k], e2.mad(yb, e_v[k], e2.mul(band_m, lsun[k]))) for k in range(3)]
                dr = [e2.sub(0.0, lsun[k]) for k in range(3)]
                n = f"%mod.sl.{tag}y{p}_{q}."
                lines += e2.lines + [
                    f"  call void @dx.op.rayQuery_TraceRayInline(i32 179, i32 %mod.sl.{tag}q, %dx.types.Handle "
                    f"%mod.sl.{tag}a, i32 0, i32 {RAY_INSTANCE_MASK}, float {o[0]}, float {o[1]}, float {o[2]}, float 0.000000e+00, "
                    f"float {dr[0]}, float {dr[1]}, float {dr[2]}, float {ray_len})",
                    f"  {n}p = call i1 @dx.op.rayQuery_Proceed.i1(i32 180, i32 %mod.sl.{tag}q)",
                    f"  {n}st = call i32 @dx.op.rayQuery_StateScalar.i32(i32 184, i32 %mod.sl.{tag}q)",
                    f"  {n}hit = icmp eq i32 {n}st, 1",
                    f"  {n}v = select i1 {n}hit, float 1.000000e+00, float 0.000000e+00",
                    f"  {n}s = fadd fast float {total}, {n}v",
                ]
                total = f"{n}s"
        lines += [
            f"  %mod.sl.{tag}rv = fmul fast float {total}, {f32(1.0 / (taps * taps))}",
            f"  br label %mod.sl.{tag}m",
            "",
            f"mod.sl.{tag}m:",
            f"  %mod.sl.{tag}rc = phi float [ %mod.sl.{tag}rv, %mod.sl.{tag}r ], [ 0.000000e+00, {block} ]",
            f"  %mod.sl.{tag}cov = call float @dx.op.binary.f32(i32 35, float {cov}, float %mod.sl.{tag}rc)",
        ]
        return lines, f"%mod.sl.{tag}cov"
    out += [
        f"  %mod.sl.map = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle {map_handle}, "
        f"%dx.types.ResourceProperties {{ i32 7, i32 {map_props} }})",
        "  br i1 %mod.sl.is0, label %mod.sl.l0, label %mod.sl.ln",
        "",
    ]

    def coverage(prefix: str, ci: str, cj: str, scale: str, taps: int) -> tuple[list[str], str]:
        """Fraction of taps x taps texels across cell (ci, cj) (level-0 cell
        units times `scale`) holding a caster within the band."""
        lines = []
        e = Emitter(prefix)
        fi, fj = e.new(f"uitofp i32 {ci} to float"), e.new(f"uitofp i32 {cj} to float")
        x0 = e.mad(e.mul(fi, scale), tu, cu)
        y0 = e.mad(e.mul(fj, scale), tv, cv)
        su, sv = e.mul(e.mul(scale, tu), 1.0 / taps), e.mul(e.mul(scale, tv), 1.0 / taps)
        lines += e.lines
        total = "0.000000e+00"
        for p in range(taps):
            for q in range(taps):
                e2 = Emitter(f"{prefix}t{p}_{q}.")
                tx = e2.mad(su, p + 0.5, x0)
                ty = e2.mad(sv, q + 0.5, y0)
                inside = [e2.cmp("oge", tx, 0.0), e2.cmp("olt", tx, size), e2.cmp("oge", ty, 0.0), e2.cmp("olt", ty, size)]
                cx, cy = e2.max(e2.min(tx, hi), 0.0), e2.max(e2.min(ty, hi), 0.0)
                n = f"%mod.{prefix}t{p}_{q}."
                lines += e2.lines + [
                    f"  {n}ix = fptosi float {cx} to i32",
                    f"  {n}iy = fptosi float {cy} to i32",
                    f"  {n}ld = call %dx.types.ResRet.f32 @dx.op.textureLoad.f32(i32 66, %dx.types.Handle %mod.sl.map, "
                    f"i32 0, i32 {n}ix, i32 {n}iy, i32 {kk}, i32 undef, i32 undef, i32 undef)",
                    f"  {n}d = extractvalue %dx.types.ResRet.f32 {n}ld, 0",
                ]
                e3 = Emitter(f"{prefix}t{p}_{q}.b")
                near = e3.cmp("olt", e3.unary(6, e3.sub(f"{n}d", d[3])), band)
                some = e3.cmp("ogt", f"{n}d", 0.0)
                lines += e3.lines + [
                    f"  {n}in0 = and i1 {inside[0]}, {inside[1]}",
                    f"  {n}in1 = and i1 {inside[2]}, {inside[3]}",
                    f"  {n}in2 = and i1 {n}in0, {n}in1",
                    f"  {n}in3 = and i1 {n}in2, {near}",
                    f"  {n}in = and i1 {n}in3, {some}",
                    f"  {n}v = select i1 {n}in, float 1.000000e+00, float 0.000000e+00",
                    f"  {n}s = fadd fast float {total}, {n}v",
                ]
                total = f"{n}s"
        lines.append(f"  %mod.{prefix}cov = fmul fast float {total}, {f32(1.0 / (taps * taps))}")
        return lines, f"%mod.{prefix}cov"

    # level 0
    lines0, cov0 = coverage("sl.ca", x, y, "1.000000e+00", SIL_TAPS0)
    lines0, cov0 = with_rays(lines0, cov0, "l0", "%mod.sl.l0", x, y, "1.000000e+00", SIL_RAYS0)
    out += ["mod.sl.l0:"] + lines0 + [
        f"  %mod.sl.ar0 = icmp eq i32 {x}, 0",
        f"  %mod.sl.ar1 = icmp eq i32 {y}, 0",
        f"  %mod.sl.ar2 = icmp eq i32 {x}, {SIL_N0 - 1}",
        f"  %mod.sl.ar3 = icmp eq i32 {y}, {SIL_N0 - 1}",
        "  %mod.sl.ar4 = or i1 %mod.sl.ar0, %mod.sl.ar1",
        "  %mod.sl.ar5 = or i1 %mod.sl.ar2, %mod.sl.ar3",
        "  %mod.sl.ar = or i1 %mod.sl.ar4, %mod.sl.ar5",
        f"  %mod.sl.av = select i1 %mod.sl.ar, float 0.000000e+00, float {cov0}",
        f"  %mod.sl.a.n0 = mul i32 {y}, {SIL_N0}",
        f"  %mod.sl.a.n = add i32 %mod.sl.a.n0, {x}",
        "  %mod.sl.a.o0 = shl i32 %mod.sl.a.n, 2",
        f"  %mod.sl.a.o = add i32 %mod.sl.a.o0, {SIL_REGION + SIL_OFFSETS[0]}",
        "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sc.u, i32 %mod.sl.a.o, i32 undef, "
        "float %mod.sl.av, float undef, float undef, float undef, i8 1, i32 4)",
        f"  %mod.sl.a.any = fcmp fast ogt float {cov0}, 0.000000e+00",
        "  br i1 %mod.sl.a.any, label %mod.sl.flag, label %mod.sl.hq",
        "",
        "mod.sl.flag:",
        f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %mod.sc.u, i32 {SIL_REGION}, i32 undef, "
        "i32 %mod.sl.fv, i32 undef, i32 undef, i32 undef, i8 1, i32 4)",
        "  br label %mod.sl.hq",
        "",
        "mod.sl.hq:",
        "  br i1 %mod.sc.xy0, label %mod.sl.hdr, label " + end,
        "",
        "mod.sl.hdr:",
    ]
    # header: rows from camera-relative world space (R: Global[134..136], world -> view)
    hl = []
    for r in range(3):
        hl.append(f"  %mod.sl.g{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {glob}, i32 {134 + r})")
        hl += [f"  %mod.sl.g{r}.{c} = extractvalue {t} %mod.sl.g{r}, {c}" for c in range(3)]
    out += hl
    eh = Emitter("sl.hd")

    def world(row):
        return [eh.mad(row[2], f"%mod.sl.g2.{c}", eh.mad(row[1], f"%mod.sl.g1.{c}", eh.mul(row[0], f"%mod.sl.g0.{c}")))
                for c in range(3)]
    iu = eh.div(1.0, eh.mul(c0, upm))
    iv = eh.div(1.0, eh.mul(c0, vpm))
    U = [eh.mul(x_, iu) for x_ in world(u)]
    V = [eh.mul(x_, iv) for x_ in world(v)]
    D = world(d)
    out += eh.lines
    mid = f32(SIL_N0 / 2)
    st = "@dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sc.u"
    out += [
        f"  call void {st}, i32 {SIL_REGION + 4}, i32 undef, float {c0}, float {dpm}, float {d[3]}, float undef, "
        "i8 7, i32 4)",
        f"  call void {st}, i32 {SIL_REGION + 16}, i32 undef, float {U[0]}, float {U[1]}, float {U[2]}, float {mid}, "
        "i8 15, i32 4)",
        f"  call void {st}, i32 {SIL_REGION + 32}, i32 undef, float {V[0]}, float {V[1]}, float {V[2]}, float {mid}, "
        "i8 15, i32 4)",
        f"  call void {st}, i32 {SIL_REGION + 48}, i32 undef, float {D[0]}, float {D[1]}, float {D[2]}, float {d[3]}, "
        "i8 15, i32 4)",
        f"  %mod.sl.hk = bitcast i32 {kk} to float",
        "  %mod.sl.hf = bitcast i32 %mod.sl.f16 to float",
        f"  call void {st}, i32 {SIL_REGION + 64}, i32 undef, float %mod.sl.hk, float {w}, float {a}, "
        "float %mod.sl.hf, i8 15, i32 4)",
        *camera_sun[0],
        "  %mod.sl.hv = bitcast i32 %mod.sl.fv to float",
        f"  call void {st}, i32 {SIL_REGION + 80}, i32 undef, float {camera_sun[1]}, float %mod.sl.hv, float undef, "
        "float undef, i8 3, i32 4)",
        "  br label " + end,
        "",
    ]
    # levels 1-6
    linesn, covn = coverage("sl.cb", "%mod.sl.i", y, "%mod.sl.sc", SIL_TAPS)
    linesn, covn = with_rays(linesn, covn, "ln", "%mod.sl.ln", "%mod.sl.i", y, "%mod.sl.sc", SIL_RAYS)
    out += [
        "mod.sl.ln:",
        "  %mod.sl.p2 = shl i32 1, %mod.sl.L",
        "  %mod.sl.sc = uitofp i32 %mod.sl.p2 to float",
    ] + linesn
    off = f"{SIL_REGION + SIL_OFFSETS[1]}"
    for L in range(2, SIL_LEVELS):
        out += [f"  %mod.sl.lo{L}c = icmp eq i32 %mod.sl.L, {L}",
                f"  %mod.sl.lo{L} = select i1 %mod.sl.lo{L}c, i32 {SIL_REGION + SIL_OFFSETS[L]}, i32 {off}"]
        off = f"%mod.sl.lo{L}"
    out += [
        "  %mod.sl.NLm = sub i32 %mod.sl.NL, 1",
        f"  %mod.sl.br0 = icmp eq i32 %mod.sl.i, 0",
        f"  %mod.sl.br1 = icmp eq i32 {y}, 0",
        f"  %mod.sl.br2 = icmp eq i32 %mod.sl.i, %mod.sl.NLm",
        f"  %mod.sl.br3 = icmp eq i32 {y}, %mod.sl.NLm",
        "  %mod.sl.br4 = or i1 %mod.sl.br0, %mod.sl.br1",
        "  %mod.sl.br5 = or i1 %mod.sl.br2, %mod.sl.br3",
        "  %mod.sl.br = or i1 %mod.sl.br4, %mod.sl.br5",
        f"  %mod.sl.bv = select i1 %mod.sl.br, float 0.000000e+00, float {covn}",
        f"  %mod.sl.b.n0 = mul i32 {y}, %mod.sl.NL",
        "  %mod.sl.b.n = add i32 %mod.sl.b.n0, %mod.sl.i",
        "  %mod.sl.b.o0 = shl i32 %mod.sl.b.n, 2",
        f"  %mod.sl.b.o = add i32 %mod.sl.b.o0, {off}",
        "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sc.u, i32 %mod.sl.b.o, i32 undef, "
        "float %mod.sl.bv, float undef, float undef, float undef, i8 1, i32 4)",
        "  br label " + end,
        "",
    ]
    assert SIL_REGION + SIL_OFFSETS[-1] <= 4 << 20
    return out


def shadow_image_lines(cb: str, map_handle: str, map_props: str, tid: dict, visibility: str) -> list[str]:
    """patch_shadow_capture's blocks mod.sc.img0 .. mod.sc.mask (ending in a
    branch to mod.sc.end), on capture with DebugCapture bits 0 and 3:
    threads (x < 4 * 192, y < 192) each write one pixel of a cascade's image,
    float2(max, smallest non-empty depth; 0 = empty) over 8 x 8 loads, and the
    threads at every 8th pixel write the pass's output (sun visibility)."""
    t = "%dx.types.CBufRet.f32"
    n = SHADOW_IMAGE
    out = [
        "mod.sc.img0:",
        "  %mod.sc.b9 = and i32 %mod.sc.flags, 9",
        "  %mod.sc.on9 = icmp eq i32 %mod.sc.b9, 9",
        f"  %mod.sc.ix = icmp ult i32 {tid[0]}, {4 * n}",
        f"  %mod.sc.iy = icmp ult i32 {tid[1]}, {n}",
        "  %mod.sc.ixy = and i1 %mod.sc.ix, %mod.sc.iy",
        "  %mod.sc.im = and i1 %mod.sc.on9, %mod.sc.ixy",
        "  br i1 %mod.sc.im, label %mod.sc.img, label %mod.sc.mask0",
        "",
        "mod.sc.img:",
        "  br i1 %mod.sc.xy0, label %mod.sc.imghdr, label %mod.sc.imgbody",
        "",
        "mod.sc.imghdr:",
        f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %mod.sc.u, i32 {DEBUG_REGION_SHADOW_IMAGE}, "
        f"i32 undef, i32 %mod.sc.stamp, i32 {n}, i32 0, i32 0, i8 15, i32 4)",
        f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %mod.sc.u, i32 {DEBUG_REGION_SHADOW_MASK}, "
        f"i32 undef, i32 %mod.sc.stamp, i32 {SHADOW_MASK_STRIDE}, i32 {SHADOW_MASK_ROWS}, i32 {SHADOW_MASK_STEP}, "
        "i8 15, i32 4)",
        "  br label %mod.sc.imgbody",
        "",
        "mod.sc.imgbody:",
        f"  %mod.sc.ik = udiv i32 {tid[0]}, {n}",
        f"  %mod.sc.ik0 = mul i32 %mod.sc.ik, {n}",
        f"  %mod.sc.ii = sub i32 {tid[0]}, %mod.sc.ik0",
        f"  %mod.sc.isz = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {cb}, i32 40)",
        f"  %mod.sc.isize = extractvalue {t} %mod.sc.isz, 0",
        f"  %mod.sc.imap = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle {map_handle}, "
        f"%dx.types.ResourceProperties {{ i32 7, i32 {map_props} }})",
    ]
    em = Emitter("sc.ie")
    fi = em.new("uitofp i32 %mod.sc.ii to float")
    fj = em.new(f"uitofp i32 {tid[1]} to float")
    block = em.mul("%mod.sc.isize", 1.0 / n)  # texels per image pixel (8 for a 1536^2 map)
    x0, y0 = em.mul(fi, block), em.mul(fj, block)
    sub = em.mul(block, 1.0 / SHADOW_CELL_TAPS)
    hi = em.sub("%mod.sc.isize", 1.0)
    out += em.lines
    best, least = "0.000000e+00", "2.000000e+00"
    for a in range(SHADOW_CELL_TAPS):
        for b in range(SHADOW_CELL_TAPS):
            e2 = Emitter(f"sc.it{a}_{b}.")
            tx = e2.max(e2.min(e2.mad(sub, a + 0.5, x0), hi), 0.0)
            ty = e2.max(e2.min(e2.mad(sub, b + 0.5, y0), hi), 0.0)
            q = f"%mod.sc.it{a}_{b}."
            out += e2.lines + [
                f"  {q}ix = fptosi float {tx} to i32",
                f"  {q}iy = fptosi float {ty} to i32",
                f"  {q}ld = call %dx.types.ResRet.f32 @dx.op.textureLoad.f32(i32 66, %dx.types.Handle %mod.sc.imap, "
                f"i32 0, i32 {q}ix, i32 {q}iy, i32 %mod.sc.ik, i32 undef, i32 undef, i32 undef)",
                f"  {q}v = extractvalue %dx.types.ResRet.f32 {q}ld, 0",
                f"  {q}m = call float @dx.op.binary.f32(i32 35, float {best}, float {q}v)",
                f"  {q}nz = fcmp fast ogt float {q}v, 0.000000e+00",
                f"  {q}w = select i1 {q}nz, float {q}v, float 2.000000e+00",
                f"  {q}l = call float @dx.op.binary.f32(i32 36, float {least}, float {q}w)",
            ]
            best, least = f"{q}m", f"{q}l"
    out += [
        f"  %mod.sc.ilo = fcmp fast olt float {least}, 1.500000e+00",
        f"  %mod.sc.ilv = select i1 %mod.sc.ilo, float {least}, float 0.000000e+00",
        f"  %mod.sc.in0 = mul i32 %mod.sc.ik, {n}",
        f"  %mod.sc.in1 = add i32 %mod.sc.in0, {tid[1]}",
        f"  %mod.sc.in2 = mul i32 %mod.sc.in1, {n}",
        "  %mod.sc.in = add i32 %mod.sc.in2, %mod.sc.ii",
        "  %mod.sc.io0 = shl i32 %mod.sc.in, 3",
        f"  %mod.sc.io = add i32 %mod.sc.io0, {DEBUG_REGION_SHADOW_IMAGE + 16}",
        "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sc.u, i32 %mod.sc.io, i32 undef, "
        f"float {best}, float %mod.sc.ilv, float undef, float undef, i8 3, i32 4)",
        "  br label %mod.sc.mask0",
        "",
        "mod.sc.mask0:",
        f"  %mod.sc.mx7 = and i32 {tid[0]}, {SHADOW_MASK_STEP - 1}",
        f"  %mod.sc.my7 = and i32 {tid[1]}, {SHADOW_MASK_STEP - 1}",
        "  %mod.sc.mxy7 = or i32 %mod.sc.mx7, %mod.sc.my7",
        "  %mod.sc.mon = icmp eq i32 %mod.sc.mxy7, 0",
        f"  %mod.sc.mx = udiv i32 {tid[0]}, {SHADOW_MASK_STEP}",
        f"  %mod.sc.my = udiv i32 {tid[1]}, {SHADOW_MASK_STEP}",
        f"  %mod.sc.mxi = icmp ult i32 %mod.sc.mx, {SHADOW_MASK_STRIDE}",
        f"  %mod.sc.myi = icmp ult i32 %mod.sc.my, {SHADOW_MASK_ROWS}",
        "  %mod.sc.mi = and i1 %mod.sc.mxi, %mod.sc.myi",
        "  %mod.sc.mg = and i1 %mod.sc.mon, %mod.sc.mi",
        "  %mod.sc.mq = and i1 %mod.sc.on9, %mod.sc.mg",
        "  br i1 %mod.sc.mq, label %mod.sc.mask, label %mod.sc.end",
        "",
        "mod.sc.mask:",
        f"  %mod.sc.mn0 = mul i32 %mod.sc.my, {SHADOW_MASK_STRIDE}",
        "  %mod.sc.mn = add i32 %mod.sc.mn0, %mod.sc.mx",
        "  %mod.sc.mo0 = shl i32 %mod.sc.mn, 2",
        f"  %mod.sc.mo = add i32 %mod.sc.mo0, {DEBUG_REGION_SHADOW_MASK + 16}",
        "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sc.u, i32 %mod.sc.mo, i32 undef, "
        f"float {visibility}, float undef, float undef, float undef, i8 1, i32 4)",
        "  br label %mod.sc.end",
        "",
    ]
    assert DEBUG_REGION_SHADOW_IMAGE + 16 + 4 * n * n * 8 <= DEBUG_REGION_SHADOW_MASK
    return out


def patch_shadow_capture(text: str) -> tuple[str, str]:
    """ch_deferred_shadows: on capture (DebugCapture bit 0), dump the sun
    shadow constants and the shadow-map depths around the camera into the
    debug buffer (u0 space50), for analysing the aircraft's shadow. Appended
    after the shader's own output, so its result is unchanged.

    The shadow cbuffer is found by its size (720 bytes). Per cascade we use a
    block of five registers whose rows 0-2 map view space to shadow-map u, v
    and depth; c40.x is the map size and c42.x the cascade count. The grid: per cascade, 16 x 16 cells around
    the camera's own shadow-map position (the view-space origin), 6 m wide
    (at most 1/16 of the map, so the small cascades are covered whole); each
    holds the maximum depth of 8 x 8 loads across it, so no occluder in the
    window slips between samples (an empty map reads 0). With bit 3 too (the
    2 MB buffer), every cascade whole as an image and the pass's output on
    screen (shadow_image_lines)."""
    ir = Ir(text)
    shadow_cb = [h for h, (_, size) in ir.cbs.items() if size == 720]
    need(len(shadow_cb) == 1, f"{len(shadow_cb)} 720-byte shadow cbuffers")
    cb = shadow_cb[0]
    glob = [h for h, (_, size) in ir.cbs.items() if size == 4800]
    need(len(glob) == 1, f"{len(glob)} Global cbuffers")
    cmp = re.search(r"@dx\.op\.sampleCmpLevelZero\.f32\(i32 65, %dx\.types\.Handle (%[\w.]+),", text)
    need(cmp, "no shadow-map comparison sample")
    annot = re.search(rf"^\s+{re.escape(cmp[1])} = call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, "
                      rf"%dx\.types\.Handle (%[\w.]+), %dx\.types\.ResourceProperties \{{ i32 7, i32 (\d+) \}}\)",
                      text, re.M)
    need(annot, "the shadow map is not a Texture2DArray")
    map_handle, map_props = annot[1], annot[2]
    tids = re.findall(r"^\s+(%[\w.]+) = call i32 @dx\.op\.threadId\.i32\(i32 93, i32 ([01])\)", text, re.M)
    tid = {int(c): n for n, c in tids}
    need(sorted(tid) == [0, 1], "no 2D thread id")
    rets = [i for i, l in enumerate(ir.lines) if l.strip() == "ret void"]
    need(len(rets) == 1, f"{len(rets)} returns")
    # the pass's output, stored at the thread's own pixel just before the return
    store = re.match(r"\s+call void @dx\.op\.textureStore\.f32\(i32 67, %dx\.types\.Handle [%\w.]+, "
                     r"i32 ([%\w.]+), i32 ([%\w.]+), i32 undef, float ([%\w.]+),", ir.lines[rets[0] - 1])
    need(store and store[1] == tid[0] and store[2] == tid[1], "the output isn't stored at the thread's pixel "
         "right before the return")
    visibility = store[3]

    t = "%dx.types.CBufRet.f32"
    out = [
        "  %mod.sc.uh = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
        f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)",
        "  %mod.sc.u = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.sc.uh, "
        "%dx.types.ResourceProperties { i32 4107, i32 0 })",
        f"  %mod.sc.r0 = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.ah, i32 0)",
        f"  %mod.sc.f0 = extractvalue {t} %mod.sc.r0, 0",
        f"  %mod.sc.f1 = extractvalue {t} %mod.sc.r0, 1",
        "  %mod.sc.flags = bitcast float %mod.sc.f0 to i32",
        "  %mod.sc.stamp = bitcast float %mod.sc.f1 to i32",
        # bit 0: capture armed; bit 2: the add-on's debug buffer is 16 KB (older add-ons: 4 KB)
        "  %mod.sc.b0 = and i32 %mod.sc.flags, 5",
        "  %mod.sc.on = icmp eq i32 %mod.sc.b0, 5",
        f"  %mod.sc.x0 = icmp eq i32 {tid[0]}, 0",
        f"  %mod.sc.y0 = icmp eq i32 {tid[1]}, 0",
        "  %mod.sc.xy0 = and i1 %mod.sc.x0, %mod.sc.y0",
        "  %mod.sc.one = and i1 %mod.sc.on, %mod.sc.xy0",
        "  br i1 %mod.sc.one, label %mod.sc.regs, label %mod.sc.grid0",
        "",
        "mod.sc.regs:",
        f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %mod.sc.u, i32 0, i32 undef, "
        f"i32 {DEBUG_MAGIC}, i32 %mod.sc.stamp, i32 0, i32 0, i8 15, i32 4)",
        f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %mod.sc.u, i32 {DEBUG_REGION_SHADOW}, "
        "i32 undef, i32 %mod.sc.stamp, i32 43, i32 0, i32 0, i8 15, i32 4)",
        f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %mod.sc.u, i32 {DEBUG_REGION_SHADOW_GRID}, "
        f"i32 undef, i32 %mod.sc.stamp, i32 {4 * SHADOW_GRID * SHADOW_GRID}, i32 0, i32 0, i8 15, i32 4)",
    ]
    for r in range(43):
        out.append(f"  %mod.sc.c{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {cb}, i32 {r})")
        comps = []
        for k in range(4):
            out.append(f"  %mod.sc.c{r}.{k} = extractvalue {t} %mod.sc.c{r}, {k}")
            comps.append(f"float %mod.sc.c{r}.{k}")
        out.append(f"  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sc.u, "
                   f"i32 {DEBUG_REGION_SHADOW + 16 + 16 * r}, i32 undef, {', '.join(comps)}, i8 15, i32 4)")
    # with the 4 MB buffer (bit 4): the whole Global cbuffer, for debugging
    out += [
        "  %mod.sc.b16 = and i32 %mod.sc.flags, 16",
        "  %mod.sc.on16 = icmp ne i32 %mod.sc.b16, 0",
        "  br i1 %mod.sc.on16, label %mod.sc.glob, label %mod.sc.grid0",
        "",
        "mod.sc.glob:",
        f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %mod.sc.u, i32 {DEBUG_REGION_GLOBAL}, "
        f"i32 undef, i32 %mod.sc.stamp, i32 {GLOBAL_REGS}, i32 0, i32 0, i8 15, i32 4)",
    ]
    for r in range(GLOBAL_REGS):
        out.append(f"  %mod.sc.g{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {glob[0]}, i32 {r})")
        comps = []
        for k in range(4):
            out.append(f"  %mod.sc.g{r}.{k} = extractvalue {t} %mod.sc.g{r}, {k}")
            comps.append(f"float %mod.sc.g{r}.{k}")
        out.append(f"  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sc.u, "
                   f"i32 {DEBUG_REGION_GLOBAL + 16 + 16 * r}, i32 undef, {', '.join(comps)}, i8 15, i32 4)")
    out += [
        "  br label %mod.sc.grid0",
        "",
        "mod.sc.grid0:",
        f"  %mod.sc.gx = icmp ult i32 {tid[0]}, {4 * SHADOW_GRID}",
        f"  %mod.sc.gy = icmp ult i32 {tid[1]}, {SHADOW_GRID}",
        "  %mod.sc.gxy = and i1 %mod.sc.gx, %mod.sc.gy",
        "  %mod.sc.g = and i1 %mod.sc.on, %mod.sc.gxy",
        "  br i1 %mod.sc.g, label %mod.sc.grid, label %mod.sc.img0",
        "",
        "mod.sc.grid:",
        f"  %mod.sc.k = lshr i32 {tid[0]}, 4",
        f"  %mod.sc.i = and i32 {tid[0]}, 15",
        "  %mod.sc.row = mul i32 %mod.sc.k, 5",
        "  %mod.sc.row1 = add i32 %mod.sc.row, 1",
        f"  %mod.sc.m0 = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {cb}, i32 %mod.sc.row)",
        f"  %mod.sc.m1 = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {cb}, i32 %mod.sc.row1)",
        f"  %mod.sc.sz = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {cb}, i32 40)",
        f"  %mod.sc.size = extractvalue {t} %mod.sc.sz, 0",
    ]
    for a in range(2):
        out += [f"  %mod.sc.m{a}{k} = extractvalue {t} %mod.sc.m{a}, {k}" for k in range(4)]
    em = Emitter("sc.e")
    # texels per metre along the map's u axis, and the grid step: 6 m, but at
    # most size / 16 so the small cascades are covered whole
    tpm = em.mul(em.sqrt(em.mad("%mod.sc.m02", "%mod.sc.m02", em.mad("%mod.sc.m01", "%mod.sc.m01",
                                                                     em.mul("%mod.sc.m00", "%mod.sc.m00")))),
                 "%mod.sc.size")
    step = em.max(em.min(em.mul(tpm, SHADOW_GRID_STEP_M), em.mul("%mod.sc.size", 1.0 / SHADOW_GRID)), 1.0)
    fi = em.new("uitofp i32 %mod.sc.i to float")
    fj = em.new(f"uitofp i32 {tid[1]} to float")
    half = SHADOW_GRID / 2.0  # cell corners: the camera sits at the corner of the middle four cells
    x0 = em.mad(em.sub(fi, half), step, em.mul("%mod.sc.m03", "%mod.sc.size"))
    y0 = em.mad(em.sub(fj, half), step, em.mul("%mod.sc.m13", "%mod.sc.size"))
    sub = em.mul(step, 1.0 / SHADOW_CELL_TAPS)
    hi = em.sub("%mod.sc.size", 1.0)
    out += em.lines
    out.append(f"  %mod.sc.map = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle {map_handle}, "
               f"%dx.types.ResourceProperties {{ i32 7, i32 {map_props} }})")
    # the cell's maximum depth over SHADOW_CELL_TAPS^2 loads covering it (empty = 0)
    best = "0.000000e+00"
    for a in range(SHADOW_CELL_TAPS):
        for b in range(SHADOW_CELL_TAPS):
            e2 = Emitter(f"sc.t{a}_{b}.")
            tx = e2.max(e2.min(e2.mad(sub, a + 0.5, x0), hi), 0.0)
            ty = e2.max(e2.min(e2.mad(sub, b + 0.5, y0), hi), 0.0)
            q = f"%mod.sc.t{a}_{b}."
            out += e2.lines + [
                f"  {q}ix = fptosi float {tx} to i32",
                f"  {q}iy = fptosi float {ty} to i32",
                f"  {q}ld = call %dx.types.ResRet.f32 @dx.op.textureLoad.f32(i32 66, %dx.types.Handle %mod.sc.map, "
                f"i32 0, i32 {q}ix, i32 {q}iy, i32 %mod.sc.k, i32 undef, i32 undef, i32 undef)",
                f"  {q}v = extractvalue %dx.types.ResRet.f32 {q}ld, 0",
                f"  {q}m = call float @dx.op.binary.f32(i32 35, float {best}, float {q}v)",
            ]
            best = f"{q}m"
    out += [
        f"  %mod.sc.dv = fadd fast float {best}, 0.000000e+00",
        f"  %mod.sc.n0 = mul i32 %mod.sc.k, {SHADOW_GRID * SHADOW_GRID}",
        f"  %mod.sc.n1 = mul i32 {tid[1]}, {SHADOW_GRID}",
        "  %mod.sc.n2 = add i32 %mod.sc.n0, %mod.sc.n1",
        "  %mod.sc.n = add i32 %mod.sc.n2, %mod.sc.i",
        "  %mod.sc.o0 = shl i32 %mod.sc.n, 2",
        f"  %mod.sc.o = add i32 %mod.sc.o0, {DEBUG_REGION_SHADOW_GRID + 16}",
        "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.sc.u, i32 %mod.sc.o, i32 undef, "
        "float %mod.sc.dv, float undef, float undef, float undef, i8 1, i32 4)",
        "  br label %mod.sc.img0",
        "",
        *shadow_image_lines(cb, map_handle, map_props, tid, visibility),
    ]
    # the capture blocks continue into the live window (silhouette_lines), then return
    out = [l.replace("label %mod.sc.end", "label %mod.sl.0") for l in out]
    camera_sun = cloud_shadow_at_camera(ir, visibility, glob[0])
    out += [*silhouette_lines(cb, glob[0], map_handle, map_props, tid, "%mod.sc.end", camera_sun),
            "mod.sc.end:",
            "  ret void"]
    ir.lines[rets[0]:rets[0] + 1] = out
    text = finish(ir, cloud=True, debug_uav=True)
    return text, (f"shadow capture and live window: cbuffer {cb}, Global {glob[0]}, shadow map {map_handle}, "
                  f"sunlight at the camera {'from the cloud-layer shadow' if camera_sun[0] else '1 (no cloud shadow)'}")


OUTPUT_REGS = 16  # cbuffer registers dumped by patch_output_capture
OUTPUT_MASK_OFFSET = 512


def patch_output_capture(text: str, region: int) -> tuple[str, str]:
    """A compute pass that stores one value at its thread's pixel just before
    returning (e.g. ch_raymarched_shadows): on capture with DebugCapture bits 0
    and 4 (the add-on's 4 MB debug buffer), dump that value at every 8th pixel
    into the debug buffer at `region` + 512 (rows of 512), and the first 16
    registers of its smallest cbuffer (its pass parameters) at `region` + 16.
    Appended after the pass's own store, so its output is unchanged."""
    ir = Ir(text)
    tids = re.findall(r"^\s+(%[\w.]+) = call i32 @dx\.op\.threadId\.i32\(i32 93, i32 ([01])\)", text, re.M)
    tid = {int(c): n for n, c in tids}
    need(sorted(tid) == [0, 1], "no 2D thread id")
    rets = [i for i, l in enumerate(ir.lines) if l.strip() == "ret void"]
    need(len(rets) == 1, f"{len(rets)} returns")
    store = re.match(r"\s+call void @dx\.op\.textureStore\.f32\(i32 67, %dx\.types\.Handle [%\w.]+, "
                     r"i32 ([%\w.]+), i32 ([%\w.]+), i32 [%\w.]+, float ([%\w.]+),", ir.lines[rets[0] - 1])
    need(store and store[1] == tid[0] and store[2] == tid[1], "the output isn't stored at the thread's pixel "
         "right before the return")
    need(region % 16 == 0 and region + OUTPUT_MASK_OFFSET + 4 * SHADOW_MASK_STRIDE * SHADOW_MASK_ROWS <= 4 << 20,
         f"region {region:#x} doesn't fit the 4 MB debug buffer")
    small = sorted((size, h) for h, (_, size) in ir.cbs.items() if size <= 16 * OUTPUT_REGS)
    t = "%dx.types.CBufRet.f32"
    out = [
        "  %mod.oc.uh = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
        f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)",
        "  %mod.oc.u = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.oc.uh, "
        "%dx.types.ResourceProperties { i32 4107, i32 0 })",
        f"  %mod.oc.r0 = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.ah, i32 0)",
        f"  %mod.oc.f0 = extractvalue {t} %mod.oc.r0, 0",
        f"  %mod.oc.f1 = extractvalue {t} %mod.oc.r0, 1",
        "  %mod.oc.flags = bitcast float %mod.oc.f0 to i32",
        "  %mod.oc.stamp = bitcast float %mod.oc.f1 to i32",
        # bit 0: capture armed; bit 4: the add-on's debug buffer is 4 MB
        "  %mod.oc.b = and i32 %mod.oc.flags, 17",
        "  %mod.oc.on = icmp eq i32 %mod.oc.b, 17",
        f"  %mod.oc.x0 = icmp eq i32 {tid[0]}, 0",
        f"  %mod.oc.y0 = icmp eq i32 {tid[1]}, 0",
        "  %mod.oc.xy0 = and i1 %mod.oc.x0, %mod.oc.y0",
        "  %mod.oc.one = and i1 %mod.oc.on, %mod.oc.xy0",
        "  br i1 %mod.oc.one, label %mod.oc.hdr, label %mod.oc.mask0",
        "",
        "mod.oc.hdr:",
        f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle %mod.oc.u, i32 {region}, i32 undef, "
        f"i32 %mod.oc.stamp, i32 {SHADOW_MASK_STRIDE}, i32 {SHADOW_MASK_ROWS}, i32 {small[0][0] if small else 0}, "
        "i8 15, i32 4)",
    ]
    if small:
        size, cb = small[0]
        for r in range(min(OUTPUT_REGS, size // 16)):
            out.append(f"  %mod.oc.c{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {cb}, i32 {r})")
            comps = []
            for k in range(4):
                out.append(f"  %mod.oc.c{r}.{k} = extractvalue {t} %mod.oc.c{r}, {k}")
                comps.append(f"float %mod.oc.c{r}.{k}")
            out.append(f"  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.oc.u, "
                       f"i32 {region + 16 + 16 * r}, i32 undef, {', '.join(comps)}, i8 15, i32 4)")
    out += [
        "  br label %mod.oc.mask0",
        "",
        "mod.oc.mask0:",
        f"  %mod.oc.mx7 = and i32 {tid[0]}, {SHADOW_MASK_STEP - 1}",
        f"  %mod.oc.my7 = and i32 {tid[1]}, {SHADOW_MASK_STEP - 1}",
        "  %mod.oc.mxy7 = or i32 %mod.oc.mx7, %mod.oc.my7",
        "  %mod.oc.mon = icmp eq i32 %mod.oc.mxy7, 0",
        f"  %mod.oc.mx = udiv i32 {tid[0]}, {SHADOW_MASK_STEP}",
        f"  %mod.oc.my = udiv i32 {tid[1]}, {SHADOW_MASK_STEP}",
        f"  %mod.oc.mxi = icmp ult i32 %mod.oc.mx, {SHADOW_MASK_STRIDE}",
        f"  %mod.oc.myi = icmp ult i32 %mod.oc.my, {SHADOW_MASK_ROWS}",
        "  %mod.oc.mi = and i1 %mod.oc.mxi, %mod.oc.myi",
        "  %mod.oc.mg = and i1 %mod.oc.mon, %mod.oc.mi",
        "  %mod.oc.mq = and i1 %mod.oc.on, %mod.oc.mg",
        "  br i1 %mod.oc.mq, label %mod.oc.mask, label %mod.oc.end",
        "",
        "mod.oc.mask:",
        f"  %mod.oc.mn0 = mul i32 %mod.oc.my, {SHADOW_MASK_STRIDE}",
        "  %mod.oc.mn = add i32 %mod.oc.mn0, %mod.oc.mx",
        "  %mod.oc.mo0 = shl i32 %mod.oc.mn, 2",
        f"  %mod.oc.mo = add i32 %mod.oc.mo0, {region + OUTPUT_MASK_OFFSET}",
        "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.oc.u, i32 %mod.oc.mo, i32 undef, "
        f"float {store[3]}, float undef, float undef, float undef, i8 1, i32 4)",
        "  br label %mod.oc.end",
        "",
        "mod.oc.end:",
        "  ret void",
    ]
    ir.lines[rets[0]:rets[0] + 1] = out
    return finish(ir, debug_uav=True), f"output capture at {region:#x}" + (
        f", cbuffer {small[0][1]} ({small[0][0]} bytes)" if small else "")


# ---- Cloud-generator capture: the weather volume and its inputs
# With DebugCapture bits 0 and 6 (capture armed, the add-on's 16 MB debug
# buffer), every ch_cloudDensity variant dumps from CLOUD_GEN_REGION (bytes):
#   0         i32 stamp, version, flags (1: the textures were dumped), N (threads per axis)
#   16        i32 x 4 per texture: getDimensions (w, h, d, levels) of t4, t5, t6, t7, t2, t3
#   128       User (cb0) registers 0..63;  1152: PostProcess (cb2) registers 0..51
#   2048      t7 texels 0..255 (.x, f32): the coverage curve
#   0x1000    t5 (the weather map, keyframe 0) texels (0..255, 0..255, 0..31) as u8, 32 B per column
#   0x201000  t6 (keyframe 1), the same
#   0x401000  t4 (the shape noise) texels (0..127, 0..127, 0..31) as RGBA8, 128 B per column
#   0x601000  the output at the 128 x 128 columns around the centre, RGBA8 per slice, 128 B per column
#   0x801000  ... and at every 4th column (128 x 128)
#   0xA01000  t2, t3 (precipitation keyframes) texels (0..255, 0..255) .x as f32, 256 KB each
#   0xA81000  per column of the centre window: float4 (local terrain t1.x, global terrain H, the METAR
#             anchor's weight, its distance to the station in m; 0, 0 without anchoring)
#   3072      the cloud constants C0..C31 as the shader sees them (8 registers)
# That is the live-weather family (flag 1: the weather map). The layer family
# (flag 2: weather presets and injected weather such as Active Sky) has its noise in
# t2 and its coverage curve in t3, dumped at the same places (getDimensions in
# the t4 and t7 slots); where it also binds a 2D map t4 (flag 4), that map's
# texels (0..255, 0..255) go to 0x1000 as RGBA f32 (16 B each), its size in
# the t2 slot.
CLOUD_GEN_REGION = 0x400000
CG_DIMS, CG_USER, CG_POST, CG_LUT = 16, 128, 1152, 2048
CG_MAP = (0x1000, 0x201000)
CG_NOISE, CG_VOL, CG_VOLSUB, CG_PRECIP, CG_COLUMNS = 0x401000, 0x601000, 0x801000, 0xA01000, 0xA81000
# the METAR anchoring's station table (apply_metar_anchor): written by the add-on,
# not by the capture, but inside the captured range so captures carry it
CG_ANCHOR, CG_END = 0xAC1000, 0xAC3000
CG_CLOUD = 3072
CG_USER_REGS, CG_POST_REGS = 64, 52
CG_MAP_N, CG_NOISE_N, CG_VOL_N, CG_LUT_N = 256, 128, 128, 256
CG_SLICES = 32
CG_VERSION = 1
CG_DIM_TEXTURES = (4, 5, 6, 7, 2, 3)  # the getDimensions slots, named after the live layout
# Generator families: t register -> shape (2 Texture2D, 4 Texture3D), and which register holds what.
CG_LAYOUTS = {
    "weather map": {"flag": 1, "shapes": {2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 2}, "noise": 4, "lut": 7,
                    "maps": (5, 6), "precip": (2, 3), "dims": (4, 5, 6, 7, 2, 3)},
    "layers": {"flag": 2, "shapes": {2: 4, 3: 2}, "noise": 2, "lut": 3, "maps": (), "precip": (),
               "dims": (2, None, None, 3, 4, None)},
}
CG_MAP2D_FLAG = 4
DEBUG_BUFFER_16MB = 64  # DebugCapture bit 6
# The renderer's own User cbuffer (ch_volumetric_clouds_render): 64 registers
# at CLOUD_USER_REGION + 16, stamp at + 0, on capture with bit 4.
CLOUD_USER_REGION = 0x3E2000
CLOUD_USER_REGS = 64
# ... then its sky light: elements 0 and 1 of the stride-144 SH
# StructuredBuffer (R, G, B, A, 9 coefficients each), raw.
CLOUD_SH_REGION = CLOUD_USER_REGION + 16 + 16 * CLOUD_USER_REGS
CLOUD_SH_ELEMENTS, CLOUD_SH_STRIDE = 2, 144
assert CLOUD_SH_REGION + CLOUD_SH_ELEMENTS * CLOUD_SH_STRIDE <= CLOUD_GEN_REGION
assert CLOUD_GEN_REGION + CG_END <= 16 << 20

RES_CLASS = {"t": 0, "u": 1, "cb": 2, "s": 3}


def binding_handle(text: str, cls: str, reg: int) -> str | None:
    """The createHandleFromBinding value of register `reg` of class cls (space 0)."""
    c = RES_CLASS[cls]
    for m in re.finditer(rf"^\s+({V}) = call %dx\.types\.Handle @dx\.op\.createHandleFromBinding\(i32 217, "
                         r"%dx\.types\.ResBind (zeroinitializer|\{ i32 (\d+), i32 \d+, i32 (\d+), i8 (\d+) \})",
                         text, re.M):
        if m[2] == "zeroinitializer":
            lo, space, k = 0, 0, 0
        else:
            lo, space, k = int(m[3]), int(m[4]), int(m[5])
        if (k, lo, space) == (c, reg, 0):
            return m[1]
    return None


def annotated(text: str, cls: str, reg: int, name: str, lines: list) -> str | None:
    """Append our own annotateHandle for that register (with the stock
    properties of its first annotation) and return its name."""
    raw = binding_handle(text, cls, reg)
    if raw is None:
        return None
    m = re.search(rf"@dx\.op\.annotateHandle\(i32 216, %dx\.types\.Handle {re.escape(raw)}, "
                  r"(%dx\.types\.ResourceProperties \{ i32 -?\d+, i32 -?\d+ \})\)", text)
    if m is None:
        return None
    lines.append(f"  {name} = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle {raw}, {m[1]})")
    return name


def srv_shapes(text: str) -> dict:
    """t register (space 0) -> resource shape (2 Texture2D, 4 Texture3D, 5 TextureCube)."""
    res = re.search(r"^!dx\.resources = !\{!(\d+)\}$", text, re.M)
    lists = re.search(rf"^!{res[1]} = !\{{(.*)\}}$", text, re.M)
    srv = lists[1].split(",")[0].strip()
    if srv == "null":
        return {}
    shapes = {}
    for e in re.search(rf"^{re.escape(srv)} = !\{{(.*)\}}$", text, re.M)[1].split(","):
        m = re.search(rf"^{re.escape(e.strip())} = !\{{i32 \d+, .*?, !\"[^\"]*\", i32 (\d+), i32 (\d+), "
                      r"i32 \d+, i32 (\d+),", text, re.M)
        if m and m[1] == "0":
            shapes[int(m[2])] = int(m[3])
    return shapes


def pack_unorm8(vals, tag: str, lines: list) -> str:
    """Four floats (IR values or literals) packed as RGBA8 UNORM, as the
    hardware stores them (round(sat(v) * 255)); returns the i32."""
    const, parts = 0, []
    for c, v in enumerate(vals):
        if not v.startswith("%"):
            const |= int(round(min(max(literal_value(v), 0.0), 1.0) * 255)) << (8 * c)
            continue
        n = f"%mod.{tag}{c}"
        lines += [f"  {n}s = call float @dx.op.unary.f32(i32 7, float {v})",
                  f"  {n}m = fmul fast float {n}s, 2.550000e+02",
                  f"  {n}a = fadd fast float {n}m, 5.000000e-01",
                  f"  {n}i = fptoui float {n}a to i32"]
        if c:
            lines.append(f"  {n}h = shl i32 {n}i, {8 * c}")
        parts.append(f"{n}h" if c else f"{n}i")
    acc = str(const)
    for k, p in enumerate(parts):
        lines.append(f"  %mod.{tag}o{k} = or i32 {acc}, {p}")
        acc = f"%mod.{tag}o{k}"
    return acc


def store_words(uav: str, offset: str, words, lines: list, tag: str):
    """Store i32 words at a byte offset (an i32 value or literal), 4 per store."""
    for g in range(0, len(words), 4):
        chunk = list(words[g:g + 4]) + ["undef"] * (4 - len(words[g:g + 4]))
        mask = (1 << min(4, len(words) - g)) - 1
        if offset.startswith("%"):
            o = f"%mod.{tag}w{g}"
            lines.append(f"  {o} = add i32 {offset}, {4 * g}")
        else:
            o = str(int(offset) + 4 * g)
        lines.append(f"  call void @dx.op.rawBufferStore.i32(i32 140, %dx.types.Handle {uav}, i32 {o}, i32 undef, "
                     + ", ".join(f"i32 {w}" for w in chunk) + f", i8 {mask}, i32 4)")


def store_cbuffer(uav: str, handle: str, regs: int, offset: int, lines: list, tag: str):
    t = "%dx.types.CBufRet.f32"
    for r in range(regs):
        lines.append(f"  %mod.{tag}{r} = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle {handle}, i32 {r})")
        comps = []
        for k in range(4):
            lines.append(f"  %mod.{tag}{r}.{k} = extractvalue {t} %mod.{tag}{r}, {k}")
            comps.append(f"float %mod.{tag}{r}.{k}")
        lines.append(f"  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle {uav}, i32 {offset + 16 * r}, "
                     f"i32 undef, {', '.join(comps)}, i8 15, i32 4)")


def capture_flag_lines(tag: str, bits: int) -> list[str]:
    """%mod.<tag>.on: the capture is armed with `bits` set; %mod.<tag>.stamp."""
    t = "%dx.types.CBufRet.f32"
    return [f"  %mod.{tag}.r0 = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.ah, i32 0)",
            f"  %mod.{tag}.f0 = extractvalue {t} %mod.{tag}.r0, 0",
            f"  %mod.{tag}.f1 = extractvalue {t} %mod.{tag}.r0, 1",
            f"  %mod.{tag}.flags = bitcast float %mod.{tag}.f0 to i32",
            f"  %mod.{tag}.stamp = bitcast float %mod.{tag}.f1 to i32",
            f"  %mod.{tag}.b = and i32 %mod.{tag}.flags, {bits}",
            f"  %mod.{tag}.on = icmp eq i32 %mod.{tag}.b, {bits}"]


# ---- METAR anchoring: near METAR stations, MSFS's
# live weather map is blended toward the layers they report. The add-on's
# weather feed keeps up to ANCHOR_STATIONS stations (the nearest one, the ones
# passed, the ones ahead: addon/src/cloud_stations.h) and writes them, already
# resolved per slice, into the debug buffer at ANCHOR_TABLE; the generator
# reads it while DebugCapture bit 6 (the 16 MB buffer) is set:
#   +0    uint4 (ANCHOR_MAGIC, stations in use, ANCHOR_STATIONS, 0)
#   +16   per station float4 (x, y, z, weight): its direction in the planet
#         frame (x = 0 N 0 E, y = 0 N 90 E, z = north) and its weight 0..1,
#         which fades it in and out
#   +16 + 16 ANCHOR_STATIONS, per station 32 rows (one per slice) float4
#         (C, a, deck, smooth): the slice's weather-map value; how much the
#         station describes it (1 below what its METAR can see, 0 above,
#         where MSFS's own map stays); how flat-topped (stratiform kinds) and
#         how far toward the smooth noise channel (stratus, altostratus).
#         Crossfades between METARs happen in the add-on, in these rows.
# Cloud constants: C20 strength (0..1, ini), C21 radius (km; 20 if <= 0): a
# station has full weight within it, a Gaussian fade of the same width beyond.
# Per column and slice, with g_i = C20 weight_i fade(d_i) and the stations'
# rows: the anchored share W = 1 - prod(1 - g_i a_i), and the values blended
# toward sum(b_i v_i) / sum(b_i) with b_i = g_i a_i / (d_i^2 + ANCHOR_IDW_KM^2)
# (inverse-distance weighting: between stations a smooth transition, at a
# station its own METAR).
ANCHOR_STATIONS = 8
ANCHOR_ROWS = 32
ANCHOR_MAGIC = 0x52484341  # 'ACHR'
ANCHOR_TABLE = CLOUD_GEN_REGION + CG_ANCHOR
ANCHOR_IDW_KM = 5.0
assert 16 + 16 * ANCHOR_STATIONS * (1 + ANCHOR_ROWS) <= CG_END - CG_ANCHOR
ANCHOR_RADIUS_DEFAULT_M = 20000.0
# decks: the share of MSFS's dome taper removed (1 = a geometrically flat top) and
# how far toward the smooth noise channel smooth decks go (1 = the smooth channel only)
DECK_FLATTEN = 0.7
DECK_SMOOTH = 0.6
# ... and a deck's top slice (if it has more than one) at most C 0.91 and from MSFS's
# cellular channel (about 75 % covered over an overcast), so the top has lumps and
# dimples one slice high instead of being a plane at a slice boundary. Applied by the add-on (cloud_anchor.h kDeckTopC) in
# the rows; here for the tests.
DECK_TOP_C = 0.91
ANCHOR_MIN_RADIUS_M = 1000.0
EARTH_RADIUS_M = 6371000.0
WEATHER_SLICES = 31


def find_weather_values(ir: Ir) -> dict:
    """Slice k -> its weather-map value C_k: the blend of the two weather-map
    keyframes by PostProcess[6].x. The slice comes from the lookup's third
    coordinate, (k + 0.5) / 32. The blend factor is checked because other
    blends match the same pattern."""
    post = {h for h, (_, size) in ir.cbs.items() if size == 832}

    def keyframe_lerp(v):
        return v in ir.extracts and ir.extracts[v][1] == 0 and ir.extracts[v][0] in ir.loads and             ir.loads[ir.extracts[v][0]] == (next(iter(post), None), 6)

    found = {}
    for name, (kind, a, b) in ir.binops.items():
        if kind != "fadd":
            continue
        for m, e0 in ((a, b), (b, a)):
            if m not in ir.binops or ir.binops[m][0] != "fmul" or e0 not in ir.resret or ir.resret[e0][1] != 0:
                continue
            if not any(keyframe_lerp(o) for o in ir.binops[m][1:]):
                continue
            diffs = [o for o in ir.binops[m][1:] if o in ir.binops and ir.binops[o][0] == "fsub"]
            if not diffs or ir.binops[diffs[0]][2] != e0 or ir.binops[diffs[0]][1] not in ir.resret:
                continue
            sample = ir.lines[ir.defs[ir.resret[e0][0]]]
            mm = re.search(r"@dx\.op\.sampleLevel\.f32\(i32 62, %dx\.types\.Handle \S+, %dx\.types\.Handle \S+, "
                           r"float \S+, float \S+, float ([0-9.e+-]+),", sample)
            if not mm:
                continue
            k = round(float(mm[1]) * 32 - 0.5)
            if 0 <= k < WEATHER_SLICES and abs(float(mm[1]) - (k + 0.5) / 32) < 1e-6:
                need(k not in found or found[k] == name, f"slice {k}: two weather values ({found.get(k)}, {name})")
                found[k] = name
    return found


def find_planet_direction(ir: Ir):
    """The column's direction from the Earth's centre, (x, y, z): the
    coordinates of the terrain-elevation cube lookup, which come in the order
    (y, z, x)."""
    cubes = set()
    for line in ir.lines:
        m = re.match(rf"\s+({V}) = call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, %dx\.types\.Handle \S+, "
                     r"%dx\.types\.ResourceProperties \{ i32 5, ", line)
        if m:
            cubes.add(m[1])
    for i, line in enumerate(ir.lines):
        m = re.match(rf"\s+({V}) = call %dx\.types\.ResRet\.f32 @dx\.op\.sampleLevel\.f32\(i32 62, %dx\.types\.Handle "
                     rf"({V}), %dx\.types\.Handle \S+, float ({V}), float ({V}), float ({V}),", line)
        if m and m[2] in cubes:
            return m[5], m[3], m[4]
    return None


def find_deck_sites(ir: Ir, values: dict) -> dict:
    """Per slice k, the sites the deck weights change: the slice's coverage
    threshold L_k (from the coverage-curve lookup of C_k), the dome taper T_k
    computed from it (every slice but the first), and the parameter t_k that
    selects the noise channel. Each is found by the constants checked below."""
    def users_of(v, pred):
        return [n for n, (kind, a, b) in ir.binops.items() if v in (a, b) and pred(kind, a, b)]

    sites = {}
    for k, c in values.items():
        u = users_of(c, lambda kind, a, b: kind == "fmul" and "0x3FEFAEE420000000" in (a, b))
        need(len(u) == 1, f"slice {k}: no coverage-curve coordinate from {c}")
        u2 = users_of(u[0], lambda kind, a, b: kind == "fadd")
        need(len(u2) == 1, f"slice {k}: coverage-curve coordinate")
        sample = next((ir.def_name(i) for i in ir.users(u2[0]) if "sampleLevel" in ir.lines[i]), None)
        need(sample, f"slice {k}: no coverage-curve lookup")
        ext = next((n for n, (res, comp) in ir.resret.items() if res == sample and comp == 0), None)
        sat = next((n for n, (op, arg) in ir.unary.items() if op == OP_SATURATE and arg == ext), None)
        L = users_of(sat, lambda kind, a, b: kind == "fmul" and "0x3FF4CCCCC0000000" in (a, b)) if sat else []
        need(len(L) == 1, f"slice {k}: no coverage threshold")
        L = L[0]
        site = {"L": L}
        taper_x = users_of(L, lambda kind, a, b: kind == "fsub" and a == "1.250000e+00")
        if taper_x:
            T = users_of(taper_x[0], lambda kind, a, b: kind == "fmul")
            need(len(T) == 1, f"slice {k}: dome taper")
            site["taper"] = T[0]
        shape = users_of(L, lambda kind, a, b: kind == "fsub" and b == L and a != "1.250000e+00")
        need(len(shape) == 1, f"slice {k}: no shape term")
        found = []

        def walk(v, depth):
            if depth > 12:
                return
            if v in ir.unary:
                walk(ir.unary[v][1], depth + 1)
                return
            if v not in ir.binops:
                return
            kind, a, b = ir.binops[v]
            if kind == "fmul" and "-3.000000e+00" in (a, b):
                found.append(a if b == "-3.000000e+00" else b)
                return
            for o in (a, b):
                if o.startswith("%"):
                    walk(o, depth + 1)
        walk(ir.binops[shape[0]][1], 0)
        need(len(set(found)) == 1, f"slice {k}: noise-channel parameter {set(found)}")
        site["t"] = found[0]
        sites[k] = site
    need(sum("taper" in s for s in sites.values()) >= WEATHER_SLICES - 1, "dome tapers missing")
    return sites


def apply_metar_anchor(ir: Ir) -> str:
    """ch_cloudDensity, live-weather family: blend the weather-map value C of
    every slice toward the METAR stations' rows (ANCHOR_TABLE, above) by
    distance. Exactly stock while C20 <= 0, without DebugCapture bit 6 or
    without the table."""
    values = find_weather_values(ir)
    need(sorted(values) == list(range(WEATHER_SLICES)), f"weather-map values for slices {sorted(values)}")
    direction = find_planet_direction(ir)
    need(direction, "no terrain-elevation cube lookup (the column's planet direction)")
    first = min(ir.defs[v] for v in values.values())
    last = max(ir.defs[v] for v in values.values())
    need(all(ir.defs[d] < first for d in direction), "the planet direction comes after the weather values")
    need(not any(l.startswith("; <label>") or re.match(r"^[\w.]+:", l) for l in ir.lines[first:last]),
         "the weather values span blocks")
    x, y, z = direction
    rr = "%dx.types.ResRet.f32"
    n = ANCHOR_STATIONS

    def load(name, offset):
        return [f"  {name}.ad = add i32 %mod.ma.base, {offset}",
                f"  {name} = call {rr} @dx.op.rawBufferLoad.f32(i32 139, %dx.types.Handle %mod.ma.u, "
                f"i32 {name}.ad, i32 undef, i8 15, i32 4)"]
    col = [
        # the table: only with the 16 MB debug buffer bound (bit 6), and only once the add-on wrote it
        "  %mod.ma.r0 = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.ah, "
        "i32 0)",
        "  %mod.ma.f0 = extractvalue %dx.types.CBufRet.f32 %mod.ma.r0, 0",
        "  %mod.ma.flags = bitcast float %mod.ma.f0 to i32",
        f"  %mod.ma.fb = and i32 %mod.ma.flags, {DEBUG_BUFFER_16MB}",
        "  %mod.ma.fon = icmp ne i32 %mod.ma.fb, 0",
        f"  %mod.ma.base = select i1 %mod.ma.fon, i32 {ANCHOR_TABLE}, i32 0",
        "  %mod.ma.uh = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
        f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)",
        "  %mod.ma.u = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.ma.uh, "
        "%dx.types.ResourceProperties { i32 4107, i32 0 })",
        *load("%mod.ma.hdr", 0),
        f"  %mod.ma.h0 = extractvalue {rr} %mod.ma.hdr, 0",
        "  %mod.ma.hm = bitcast float %mod.ma.h0 to i32",
        f"  %mod.ma.ok0 = icmp eq i32 %mod.ma.hm, {ANCHOR_MAGIC}",
        "  %mod.ma.ok = and i1 %mod.ma.ok0, %mod.ma.fon",
        "  %mod.ma.s = call float @dx.op.unary.f32(i32 7, float %mod.C20)",
        "  %mod.ma.son = fcmp fast ogt float %mod.ma.s, 0.000000e+00",
        "  %mod.ma.on = and i1 %mod.ma.ok, %mod.ma.son",
        # the column's direction
        f"  %mod.ma.xx = fmul fast float {x}, {x}",
        f"  %mod.ma.xy = call float @dx.op.tertiary.f32(i32 46, float {y}, float {y}, float %mod.ma.xx)",
        f"  %mod.ma.xz = call float @dx.op.tertiary.f32(i32 46, float {z}, float {z}, float %mod.ma.xy)",
        "  %mod.ma.rint = call float @dx.op.unary.f32(i32 25, float %mod.ma.xz)",
        f"  %mod.ma.ux = fmul fast float {x}, %mod.ma.rint",
        f"  %mod.ma.uy = fmul fast float {y}, %mod.ma.rint",
        f"  %mod.ma.uz = fmul fast float {z}, %mod.ma.rint",
        # the radius: full weight within it, then exp(-((d - r) / r)^2)
        "  %mod.ma.rk = fmul fast float %mod.C21, 1.000000e+03",
        f"  %mod.ma.rm = call float @dx.op.binary.f32(i32 35, float %mod.ma.rk, float {f32(ANCHOR_MIN_RADIUS_M)})",
        "  %mod.ma.rz = fcmp fast ole float %mod.C21, 0.000000e+00",
        f"  %mod.ma.r = select i1 %mod.ma.rz, float {f32(ANCHOR_RADIUS_DEFAULT_M)}, float %mod.ma.rm",
    ]
    # per station: its weight g at this column, its inverse-distance weight b; for the capture's
    # column record the column's total weight 1 - prod(1 - g) and the nearest station in use
    q_all, dmin = "1.000000e+00", "1.000000e+09"
    for i in range(n):
        p = f"%mod.ma.s{i}"
        col += load(p, 16 + 16 * i)
        col += [f"  {p}.{c} = extractvalue {rr} {p}, {k}" for k, c in enumerate("xyzw")]
        col += [
            f"  {p}dx = fsub fast float %mod.ma.ux, {p}.x",
            f"  {p}dy = fsub fast float %mod.ma.uy, {p}.y",
            f"  {p}dz = fsub fast float %mod.ma.uz, {p}.z",
            f"  {p}d2 = fmul fast float {p}dx, {p}dx",
            f"  {p}d3 = call float @dx.op.tertiary.f32(i32 46, float {p}dy, float {p}dy, float {p}d2)",
            f"  {p}d4 = call float @dx.op.tertiary.f32(i32 46, float {p}dz, float {p}dz, float {p}d3)",
            f"  {p}d5 = call float @dx.op.unary.f32(i32 24, float {p}d4)",
            f"  {p}dist = fmul fast float {p}d5, {f32(EARTH_RADIUS_M)}",
            f"  {p}e0 = fsub fast float {p}dist, %mod.ma.r",
            f"  {p}e1 = call float @dx.op.binary.f32(i32 35, float {p}e0, float 0.000000e+00)",
            f"  {p}e2 = fdiv fast float {p}e1, %mod.ma.r",
            f"  {p}e3 = fmul fast float {p}e2, {p}e2",
            f"  {p}e4 = fmul fast float {p}e3, {NEG_LOG2E}",
            f"  {p}fd = call float @dx.op.unary.f32(i32 21, float {p}e4)",
            f"  {p}ws = call float @dx.op.unary.f32(i32 7, float {p}.w)",
            f"  {p}g0 = fmul fast float {p}fd, {p}ws",
            f"  {p}g1 = fmul fast float {p}g0, %mod.ma.s",
            f"  {p}g = select i1 %mod.ma.on, float {p}g1, float 0.000000e+00",
            f"  {p}dk = fmul fast float {p}dist, {f32(1e-3)}",
            f"  {p}dk2 = call float @dx.op.tertiary.f32(i32 46, float {p}dk, float {p}dk, "
            f"float {f32(ANCHOR_IDW_KM ** 2)})",
            f"  {p}b = fdiv fast float {p}g, {p}dk2",
            f"  {p}ng = fsub fast float 1.000000e+00, {p}g",
            f"  {p}q = fmul fast float {q_all}, {p}ng",
            f"  {p}use0 = fcmp fast ogt float {p}ws, 0.000000e+00",
            f"  {p}use = and i1 {p}use0, %mod.ma.on",
            f"  {p}dd = select i1 {p}use, float {p}dist, float 1.000000e+09",
            f"  {p}dm = call float @dx.op.binary.f32(i32 36, float {dmin}, float {p}dd)",
        ]
        q_all, dmin = f"{p}q", f"{p}dm"
    col += [f"  %mod.ma.w = fsub fast float 1.000000e+00, {q_all}",
            f"  %mod.ma.dist = fadd fast float {dmin}, 0.000000e+00"]
    edits = []
    rows = 16 + 16 * n
    for k, value in values.items():
        i0 = ir.defs[value]
        q = f"%mod.ma.k{k}."
        lines = (col if i0 == first else []) + [ir.lines[i0].replace(f"{value} = ", f"{q}c = ", 1)]
        p_acc, sw, sc, sst, ssm = "1.000000e+00", "0.000000e+00", "0.000000e+00", "0.000000e+00", "0.000000e+00"
        for i in range(n):
            r, st = f"{q}{i}", f"%mod.ma.s{i}"
            lines += load(f"{r}row", rows + 16 * (ANCHOR_ROWS * i + k))
            lines += [f"  {r}{c} = extractvalue {rr} {r}row, {j}" for j, c in enumerate(("v", "a", "st", "sm"))]
            lines += [
                f"  {r}e = fmul fast float {st}g, {r}a",
                f"  {r}ne = fsub fast float 1.000000e+00, {r}e",
                f"  {r}p = fmul fast float {p_acc}, {r}ne",
                f"  {r}wb = fmul fast float {st}b, {r}a",
                f"  {r}sw = fadd fast float {sw}, {r}wb",
                f"  {r}sc = call float @dx.op.tertiary.f32(i32 46, float {r}wb, float {r}v, float {sc})",
                f"  {r}sst = call float @dx.op.tertiary.f32(i32 46, float {r}wb, float {r}st, float {sst})",
                f"  {r}ssm = call float @dx.op.tertiary.f32(i32 46, float {r}wb, float {r}sm, float {ssm})",
            ]
            p_acc, sw, sc, sst, ssm = f"{r}p", f"{r}sw", f"{r}sc", f"{r}sst", f"{r}ssm"
        lines += [
            f"  {q}W = fsub fast float 1.000000e+00, {p_acc}",
            f"  {q}swm = call float @dx.op.binary.f32(i32 35, float {sw}, float {f32(1e-30)})",
            f"  {q}inv = fdiv fast float 1.000000e+00, {q}swm",
            f"  {q}T = fmul fast float {sc}, {q}inv",
            f"  {q}d = fsub fast float {q}T, {q}c",
            f"  {q}bl = call float @dx.op.tertiary.f32(i32 46, float {q}d, float {q}W, float {q}c)",
            f"  {value} = select i1 %mod.ma.on, float {q}bl, float {q}c",
            # the deck weights: flatter tops (stratiform kinds), the smooth channel (smooth decks); part of
            # MSFS's taper and cellular channels stays, so deck tops undulate instead of being a flat plane
            f"  {q}ST = fmul fast float {sst}, {q}inv",
            f"  {q}st0 = fmul fast float {q}ST, {q}W",
            f"  {q}st1 = fmul fast float {q}st0, {f32(DECK_FLATTEN)}",
            f"  {q}strat = select i1 %mod.ma.on, float {q}st1, float 0.000000e+00",
            f"  {q}SM = fmul fast float {ssm}, {q}inv",
            f"  {q}sm0 = fmul fast float {q}SM, {q}W",
            f"  {q}sm1 = fmul fast float {q}sm0, {f32(DECK_SMOOTH)}",
            f"  {q}smooth = select i1 %mod.ma.on, float {q}sm1, float 0.000000e+00",
        ]
        edits.append((i0, lines))
    # flat tops and smooth decks: per slice, scale the dome taper and move the
    # noise-channel parameter toward the smooth channel
    decks = find_deck_sites(ir, values)
    for k, site in decks.items():
        q = f"%mod.ma.k{k}."
        if "taper" in site:
            T = site["taper"]
            j = ir.defs[T]
            edits.append((j, [
                ir.lines[j].replace(f"{T} = ", f"{q}T0 = ", 1),
                f"  {q}ns = fsub fast float 1.000000e+00, {q}strat",
                f"  {q}T1 = fmul fast float {q}T0, {q}ns",
                f"  {q}son = fcmp fast ogt float {q}strat, 0.000000e+00",
                f"  {T} = select i1 {q}son, float {q}T1, float {q}T0",
            ]))
        t = site["t"]
        j = ir.defs[t]
        edits.append((j, [
            ir.lines[j].replace(f"{t} = ", f"{q}t0 = ", 1),
            f"  {q}tr = fsub fast float 1.000000e+00, {q}t0",
            f"  {q}t1 = call float @dx.op.tertiary.f32(i32 46, float {q}tr, float {q}smooth, float {q}t0)",
            f"  {q}mon = fcmp fast ogt float {q}smooth, 0.000000e+00",
            f"  {t} = select i1 {q}mon, float {q}t1, float {q}t0",
        ]))
    for i, new_lines in sorted(edits, key=lambda e: -e[0]):
        ir.lines[i:i + 1] = new_lines
    return (f"METAR anchoring: {len(values)} weather values from {n} stations at {ANCHOR_TABLE:#x}, planet direction "
            f"{x} {y} {z}; decks: {sum('taper' in s for s in decks.values())} tapers, {len(decks)} channel parameters")


def column_terrain(ir: Ir, text: str) -> tuple:
    """The column's local and global terrain heights (from t1 and t0) as the
    generator reads them, or 0 where not found."""
    out = []
    for reg, scale in ((1, None), (0, "6.553500e+04")):
        raw = binding_handle(text, "t", reg)
        found = "0.000000e+00"
        if raw:
            ann = {m[1] for m in re.finditer(rf"^\s+({V}) = call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, "
                                             rf"%dx\.types\.Handle {re.escape(raw)},", text, re.M)}
            for name, (res, comp) in ir.resret.items():
                line = ir.lines[ir.defs[res]] if res in ir.defs else ""
                m = re.search(rf"@dx\.op\.sampleLevel\.f32\(i32 62, %dx\.types\.Handle ({V}),", line)
                if comp == 0 and m and m[1] in ann:
                    if scale is None:
                        found = name
                    else:
                        users = [n for n, (k, a, b) in ir.binops.items() if k == "fmul" and name in (a, b)
                                 and scale in (a, b)]
                        found = users[0] if users else found
                    break
        out.append(found)
    return tuple(out)


def patch_cloud_gen_capture(text: str) -> tuple[str, str]:
    """ch_cloudDensity, every variant: on capture (DebugCapture bits 0 and 6)
    dump its constants, its input textures and its output (layout above) into
    the debug buffer, appended after the stores and before the return. In the
    live-weather family also the METAR anchoring (apply_metar_anchor, C20-C31),
    stock while C20 <= 0."""
    ir = Ir(text)
    anchor = "no METAR anchoring (not the live-weather family)"
    if all(srv_shapes(text).get(reg) == sh for reg, sh in CG_LAYOUTS["weather map"]["shapes"].items()):
        anchor = apply_metar_anchor(ir)
        ir.reindex()
    tids = re.findall(r"^\s+(%[\w.]+) = call i32 @dx\.op\.threadId\.i32\(i32 93, i32 ([01])\)", text, re.M)
    tid = {int(c): n for n, c in tids}
    need(sorted(tid) == [0, 1], "no 2D thread id")
    tx, ty = tid[0], tid[1]
    rets = [i for i, l in enumerate(ir.lines) if l.strip() == "ret void"]
    need(len(rets) == 1, f"{len(rets)} returns")
    stores = []
    for i, l in enumerate(ir.lines):
        m = re.match(r"\s+call void @dx\.op\.textureStore\.f32\(i32 67, %dx\.types\.Handle [%\w.]+, "
                     rf"i32 {re.escape(tx)}, i32 {re.escape(ty)}, i32 (\d+), float ([^,]+), float ([^,]+), "
                     r"float ([^,]+), float ([^,]+), i8 15\)", l)
        if m:
            stores.append((i, int(m[1]), [m[2], m[3], m[4], m[5]]))
    need(sorted(k for _, k, _ in stores) == list(range(CG_SLICES)), "not 32 slice stores at the thread's column")
    first, last = min(i for i, _, _ in stores), max(i for i, _, _ in stores)
    need(not any(l.startswith("; <label>") or re.match(r"^[\w.]+:", l) for l in ir.lines[first:last]),
         "the slice stores span blocks")
    br = next(i for i in range(last + 1, len(ir.lines)) if ir.lines[i].strip().startswith(("br ", "ret ")))
    need(re.match(r"\s+br label %[\w.]+$", ir.lines[br]), "the slice stores don't end in an unconditional branch")
    shapes = srv_shapes(text)
    layout = next((name for name, lay in CG_LAYOUTS.items()
                   if all(shapes.get(reg) == sh for reg, sh in lay["shapes"].items())), None)
    lay = CG_LAYOUTS.get(layout, {})
    map2d = layout == "layers" and shapes.get(4) == 2
    flags = lay.get("flag", 0) | (CG_MAP2D_FLAG if map2d else 0)
    base = CLOUD_GEN_REGION
    uav_lines = ["  %mod.cg.uh = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
                 f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)",
                 "  %mod.cg.u = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.cg.uh, "
                 "%dx.types.ResourceProperties { i32 4107, i32 0 })"]

    # ---- the output, right after its stores: centre window and every 4th column
    vals = {k: v for _, k, v in stores}
    out = capture_flag_lines("cv", 1 | DEBUG_BUFFER_16MB)
    t = "%dx.types.CBufRet.f32"
    user0 = []
    user = annotated(text, "cb", 0, "%mod.cv.user", user0)
    need(user, "no User cbuffer (cb0)")
    out += user0 + [
        f"  %mod.cv.u1 = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.cv.user, i32 1)",
        f"  %mod.cv.inv = extractvalue {t} %mod.cv.u1, 0",
        "  %mod.cv.nf = fdiv fast float 1.000000e+00, %mod.cv.inv",
        "  %mod.cv.nr = fadd fast float %mod.cv.nf, 5.000000e-01",
        "  %mod.cv.n = fptoui float %mod.cv.nr to i32",
        "  %mod.cv.half = lshr i32 %mod.cv.n, 1",
        f"  %mod.cv.lo = sub i32 %mod.cv.half, {CG_VOL_N // 2}",
        f"  %mod.cv.dx = sub i32 {tx}, %mod.cv.lo",
        f"  %mod.cv.dy = sub i32 {ty}, %mod.cv.lo",
        f"  %mod.cv.icx = icmp ult i32 %mod.cv.dx, {CG_VOL_N}",
        f"  %mod.cv.icy = icmp ult i32 %mod.cv.dy, {CG_VOL_N}",
        "  %mod.cv.ic = and i1 %mod.cv.icx, %mod.cv.icy",
        f"  %mod.cv.mx = and i32 {tx}, 3",
        f"  %mod.cv.my = and i32 {ty}, 3",
        "  %mod.cv.mxy = or i32 %mod.cv.mx, %mod.cv.my",
        "  %mod.cv.m0 = icmp eq i32 %mod.cv.mxy, 0",
        f"  %mod.cv.sx = lshr i32 {tx}, 2",
        f"  %mod.cv.sy = lshr i32 {ty}, 2",
        f"  %mod.cv.isx = icmp ult i32 %mod.cv.sx, {CG_VOL_N}",
        f"  %mod.cv.isy = icmp ult i32 %mod.cv.sy, {CG_VOL_N}",
        "  %mod.cv.is0 = and i1 %mod.cv.isx, %mod.cv.isy",
        "  %mod.cv.is = and i1 %mod.cv.is0, %mod.cv.m0",
        "  %mod.cv.any = or i1 %mod.cv.ic, %mod.cv.is",
        "  %mod.cv.go = and i1 %mod.cv.on, %mod.cv.any",
        "  br i1 %mod.cv.go, label %mod.cgb.vol, label %mod.cgb.vend",
        "",
        "mod.cgb.vol:",
    ]
    words = [pack_unorm8(vals[k], f"cv.p{k}.", out) for k in range(CG_SLICES)]
    out += [
        "  br i1 %mod.cv.ic, label %mod.cgb.vc, label %mod.cgb.vs0",
        "",
        "mod.cgb.vc:",
        f"  %mod.cv.ci0 = mul i32 %mod.cv.dy, {CG_VOL_N}",
        "  %mod.cv.ci = add i32 %mod.cv.ci0, %mod.cv.dx",
        f"  %mod.cv.co0 = mul i32 %mod.cv.ci, {4 * CG_SLICES}",
        f"  %mod.cv.co = add i32 %mod.cv.co0, {base + CG_VOL}",
    ]
    out += ["  %mod.cv.uh = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
            f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)",
            "  %mod.cv.u = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.cv.uh, "
            "%dx.types.ResourceProperties { i32 4107, i32 0 })"]
    store_words("%mod.cv.u", "%mod.cv.co", words, out, "cv.c")
    terrain = column_terrain(ir, text)
    weight = ("%mod.ma.w", "%mod.ma.dist") if "%mod.ma.w = " in "\n".join(ir.lines) else ("0.000000e+00",) * 2
    out += [f"  %mod.cv.tofs0 = shl i32 %mod.cv.ci, 4",
            f"  %mod.cv.tofs = add i32 %mod.cv.tofs0, {base + CG_COLUMNS}",
            "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.cv.u, i32 %mod.cv.tofs, i32 undef, "
            + ", ".join(f"float {v}" for v in (*terrain, *weight)) + ", i8 15, i32 4)"]
    out += [
        "  br label %mod.cgb.vs0",
        "",
        "mod.cgb.vs0:",
        "  br i1 %mod.cv.is, label %mod.cgb.vs, label %mod.cgb.vend",
        "",
        "mod.cgb.vs:",
        f"  %mod.cv.si0 = mul i32 %mod.cv.sy, {CG_VOL_N}",
        "  %mod.cv.si = add i32 %mod.cv.si0, %mod.cv.sx",
        f"  %mod.cv.so0 = mul i32 %mod.cv.si, {4 * CG_SLICES}",
        f"  %mod.cv.so = add i32 %mod.cv.so0, {base + CG_VOLSUB}",
        "  %mod.cv.vh = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
        f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)",
        "  %mod.cv.v = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.cv.vh, "
        "%dx.types.ResourceProperties { i32 4107, i32 0 })",
    ]
    store_words("%mod.cv.v", "%mod.cv.so", words, out, "cv.s")
    out += [
        "  br label %mod.cgb.vend",
        "",
        "mod.cgb.vend:",
        ir.lines[br],
    ]
    ir.lines[br:br + 1] = out

    # ---- header, cbuffers and textures, before the return (every thread)
    rets = [i for i, l in enumerate(ir.lines) if l.strip() == "ret void"]
    r = capture_flag_lines("cg", 1 | DEBUG_BUFFER_16MB) + [
        "  br i1 %mod.cg.on, label %mod.cgb.t, label %mod.cgb.end",
        "",
        "mod.cgb.t:",
    ] + uav_lines
    handles = {}
    if layout:
        for reg in sorted({lay["noise"], lay["lut"], *lay["maps"], *lay["precip"]} | ({4} if map2d else set())):
            handles[reg] = annotated(text, "t", reg, f"%mod.cg.t{reg}", r)
            need(handles[reg], f"no annotation for t{reg}")
    hdr = []
    need(annotated(text, "cb", 0, "%mod.cg.user", hdr), "no User cbuffer (cb0)")
    post = annotated(text, "cb", 2, "%mod.cg.post", hdr)
    hdr += [
        f"  %mod.cg.u1 = call {t} @dx.op.cbufferLoadLegacy.f32(i32 59, %dx.types.Handle %mod.cg.user, i32 1)",
        f"  %mod.cg.inv = extractvalue {t} %mod.cg.u1, 0",
        "  %mod.cg.nf = fdiv fast float 1.000000e+00, %mod.cg.inv",
        "  %mod.cg.nr = fadd fast float %mod.cg.nf, 5.000000e-01",
        "  %mod.cg.n = fptoui float %mod.cg.nr to i32",
    ]
    store_words("%mod.cg.u", str(base), ["%mod.cg.stamp", str(CG_VERSION), str(flags), "%mod.cg.n"], hdr, "cg.h")
    if layout:
        for j, reg in enumerate(lay["dims"]):
            if reg not in handles:
                store_words("%mod.cg.u", str(base + CG_DIMS + 16 * j), ["0"] * 4, hdr, f"cg.dz{j}")
                continue
            hdr.append(f"  %mod.cg.d{reg} = call %dx.types.Dimensions @dx.op.getDimensions(i32 72, "
                       f"%dx.types.Handle {handles[reg]}, i32 0)")
            dims = []
            for k in range(4):
                if k == 2 and shapes[reg] == 2:  # (the validator rejects reading a 2D texture's depth)
                    dims.append("0")
                    continue
                hdr.append(f"  %mod.cg.d{reg}.{k} = extractvalue %dx.types.Dimensions %mod.cg.d{reg}, {k}")
                dims.append(f"%mod.cg.d{reg}.{k}")
            store_words("%mod.cg.u", str(base + CG_DIMS + 16 * j), dims, hdr, f"cg.dd{reg}")
    store_cbuffer("%mod.cg.u", "%mod.cg.user", CG_USER_REGS, base + CG_USER, hdr, "cg.ur")
    store_cbuffer("%mod.cg.u", "%mod.cah", CLOUD_PARAMS_SIZE // 16, base + CG_CLOUD, hdr, "cg.cr")
    if post:
        store_cbuffer("%mod.cg.u", "%mod.cg.post", CG_POST_REGS, base + CG_POST, hdr, "cg.pr")
    r += [
        f"  %mod.cg.x0 = icmp eq i32 {tx}, 0",
        f"  %mod.cg.y0 = icmp eq i32 {ty}, 0",
        "  %mod.cg.xy0 = and i1 %mod.cg.x0, %mod.cg.y0",
        "  br i1 %mod.cg.xy0, label %mod.cgb.hdr, label %mod.cgb.tex",
        "",
        "mod.cgb.hdr:",
    ] + hdr + [
        "  br label %mod.cgb.tex",
        "",
        "mod.cgb.tex:",
    ]
    if layout:
        rr = "%dx.types.ResRet.f32"
        r += [
            f"  %mod.cg.lx = icmp ult i32 {tx}, {CG_LUT_N}",
            "  %mod.cg.lut = and i1 %mod.cg.lx, %mod.cg.y0",
            "  br i1 %mod.cg.lut, label %mod.cgb.lut, label %mod.cgb.map0",
            "",
            "mod.cgb.lut:",
            f"  %mod.cg.lr = call {rr} @dx.op.textureLoad.f32(i32 66, %dx.types.Handle {handles[lay['lut']]}, i32 0, "
            f"i32 {tx}, i32 0, i32 undef, i32 undef, i32 undef, i32 undef)",
            f"  %mod.cg.lv = extractvalue {rr} %mod.cg.lr, 0",
            f"  %mod.cg.lo0 = shl i32 {tx}, 2",
            f"  %mod.cg.lo = add i32 %mod.cg.lo0, {base + CG_LUT}",
            "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.cg.u, i32 %mod.cg.lo, i32 undef, "
            "float %mod.cg.lv, float undef, float undef, float undef, i8 1, i32 4)",
            "  br label %mod.cgb.map0",
            "",
            "mod.cgb.map0:",
            f"  %mod.cg.mxi = icmp ult i32 {tx}, {CG_MAP_N}",
            f"  %mod.cg.myi = icmp ult i32 {ty}, {CG_MAP_N}",
            "  %mod.cg.mi = and i1 %mod.cg.mxi, %mod.cg.myi",
            "  br i1 %mod.cg.mi, label %mod.cgb.map, label %mod.cgb.noise0",
            "",
            "mod.cgb.map:",
            f"  %mod.cg.mc0 = mul i32 {ty}, {CG_MAP_N}",
            f"  %mod.cg.mc = add i32 %mod.cg.mc0, {tx}",
        ]
        for j, reg in enumerate(lay["maps"]):
            slices = []
            for k in range(CG_SLICES):
                r += [f"  %mod.cg.m{reg}r{k} = call {rr} @dx.op.textureLoad.f32(i32 66, %dx.types.Handle "
                      f"{handles[reg]}, i32 0, i32 {tx}, i32 {ty}, i32 {k}, i32 undef, i32 undef, i32 undef)",
                      f"  %mod.cg.m{reg}v{k} = extractvalue {rr} %mod.cg.m{reg}r{k}, 0"]
                slices.append(f"%mod.cg.m{reg}v{k}")
            words = [pack_unorm8(slices[q:q + 4], f"cg.m{reg}p{q}.", r) for q in range(0, CG_SLICES, 4)]
            r += [f"  %mod.cg.m{reg}o0 = mul i32 %mod.cg.mc, {CG_SLICES}",
                  f"  %mod.cg.m{reg}o = add i32 %mod.cg.m{reg}o0, {base + CG_MAP[j]}"]
            store_words("%mod.cg.u", f"%mod.cg.m{reg}o", words, r, f"cg.m{reg}s")
        if map2d:
            r += [f"  %mod.cg.g4r = call {rr} @dx.op.textureLoad.f32(i32 66, %dx.types.Handle {handles[4]}, "
                  f"i32 0, i32 {tx}, i32 {ty}, i32 undef, i32 undef, i32 undef, i32 undef)"]
            r += [f"  %mod.cg.g4r.{c} = extractvalue {rr} %mod.cg.g4r, {c}" for c in range(4)]
            r += ["  %mod.cg.g4o0 = shl i32 %mod.cg.mc, 4",
                  f"  %mod.cg.g4o = add i32 %mod.cg.g4o0, {base + CG_MAP[0]}",
                  "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.cg.u, i32 %mod.cg.g4o, "
                  "i32 undef, " + ", ".join(f"float %mod.cg.g4r.{c}" for c in range(4)) + ", i8 15, i32 4)"]
        for j, reg in enumerate(lay["precip"]):
            r += [f"  %mod.cg.q{reg}r = call {rr} @dx.op.textureLoad.f32(i32 66, %dx.types.Handle {handles[reg]}, "
                  f"i32 0, i32 {tx}, i32 {ty}, i32 undef, i32 undef, i32 undef, i32 undef)",
                  f"  %mod.cg.q{reg}v = extractvalue {rr} %mod.cg.q{reg}r, 0",
                  f"  %mod.cg.q{reg}o0 = shl i32 %mod.cg.mc, 2",
                  f"  %mod.cg.q{reg}o = add i32 %mod.cg.q{reg}o0, {base + CG_PRECIP + 4 * CG_MAP_N * CG_MAP_N * j}",
                  "  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.cg.u, "
                  f"i32 %mod.cg.q{reg}o, i32 undef, float %mod.cg.q{reg}v, float undef, float undef, float undef, "
                  "i8 1, i32 4)"]
        r += [
            "  br label %mod.cgb.noise0",
            "",
            "mod.cgb.noise0:",
            f"  %mod.cg.nxi = icmp ult i32 {tx}, {CG_NOISE_N}",
            f"  %mod.cg.nyi = icmp ult i32 {ty}, {CG_NOISE_N}",
            "  %mod.cg.ni = and i1 %mod.cg.nxi, %mod.cg.nyi",
            "  br i1 %mod.cg.ni, label %mod.cgb.noise, label %mod.cgb.end",
            "",
            "mod.cgb.noise:",
        ]
        words = []
        for k in range(CG_SLICES):
            r.append(f"  %mod.cg.n4r{k} = call {rr} @dx.op.textureLoad.f32(i32 66, "
                     f"%dx.types.Handle {handles[lay['noise']]}, "
                     f"i32 0, i32 {tx}, i32 {ty}, i32 {k}, i32 undef, i32 undef, i32 undef)")
            comps = []
            for c in range(4):
                r.append(f"  %mod.cg.n4r{k}.{c} = extractvalue {rr} %mod.cg.n4r{k}, {c}")
                comps.append(f"%mod.cg.n4r{k}.{c}")
            words.append(pack_unorm8(comps, f"cg.n4p{k}.", r))
        r += [f"  %mod.cg.nc0 = mul i32 {ty}, {CG_NOISE_N}",
              f"  %mod.cg.nc = add i32 %mod.cg.nc0, {tx}",
              f"  %mod.cg.no0 = mul i32 %mod.cg.nc, {4 * CG_SLICES}",
              f"  %mod.cg.no = add i32 %mod.cg.no0, {base + CG_NOISE}"]
        store_words("%mod.cg.u", "%mod.cg.no", words, r, "cg.ns")
        r.append("  br label %mod.cgb.end")
    else:
        r.append("  br label %mod.cgb.end")
    r += ["", "mod.cgb.end:", "  ret void"]
    ir.lines[rets[0]:rets[0] + 1] = r
    return finish(ir, cloud=True, debug_uav=True), (f"{anchor}; capture at {CLOUD_GEN_REGION:#x}: constants, output"
                                        + (f"; {layout} family: noise t{lay['noise']}, coverage curve t{lay['lut']}"
                                           + (", weather maps, precipitation" if lay["maps"] else "")
                                           + (", 2D map t4" if map2d else "") if layout else
                                           " (unknown texture layout: no textures)"))


def append_user_dump(ir: Ir) -> str:
    """ch_volumetric_clouds_render: on capture (bits 0 and 4) thread (0, 0)
    dumps its User cbuffer at CLOUD_USER_REGION, and its sky light (SH elements 0 and 1) at
    CLOUD_SH_REGION. Only where the shader has a single return."""
    text = "\n".join(ir.lines)
    rets = [i for i, l in enumerate(ir.lines) if l.strip() == "ret void"]
    tids = dict((int(c), n) for n, c in re.findall(
        r"^\s+(%[\w.]+) = call i32 @dx\.op\.threadId\.i32\(i32 93, i32 ([01])\)", text, re.M))
    if len(rets) != 1 or sorted(tids) != [0, 1]:
        return "no User dump"
    lines = capture_flag_lines("ud", 17)
    lines += [
        f"  %mod.ud.x0 = icmp eq i32 {tids[0]}, 0",
        f"  %mod.ud.y0 = icmp eq i32 {tids[1]}, 0",
        "  %mod.ud.xy0 = and i1 %mod.ud.x0, %mod.ud.y0",
        "  %mod.ud.go = and i1 %mod.ud.on, %mod.ud.xy0",
        "  br i1 %mod.ud.go, label %mod.udb.w, label %mod.udb.end",
        "",
        "mod.udb.w:",
        "  %mod.ud.uh = call %dx.types.Handle @dx.op.createHandleFromBinding(i32 217, %dx.types.ResBind "
        f"{{ i32 0, i32 0, i32 {MOD_SPACE}, i8 1 }}, i32 0, i1 false)",
        "  %mod.ud.u = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle %mod.ud.uh, "
        "%dx.types.ResourceProperties { i32 4107, i32 0 })",
    ]
    if not annotated(text, "cb", 0, "%mod.ud.user", lines):
        return "no User dump (no cb0)"
    store_words("%mod.ud.u", str(CLOUD_USER_REGION), ["%mod.ud.stamp"], lines, "ud.h")
    store_cbuffer("%mod.ud.u", "%mod.ud.user", CLOUD_USER_REGS, CLOUD_USER_REGION + 16, lines, "ud.reg")
    sh = re.search(rf"@dx\.op\.annotateHandle\(i32 216, %dx\.types\.Handle ({V}), %dx\.types\.ResourceProperties "
                   rf"\{{ i32 524, i32 {CLOUD_SH_STRIDE} \}}\)", text)
    if sh and sh[1] in {m[1] for m in re.finditer(rf"^\s+({V}) = call %dx\.types\.Handle "
                                                   r"@dx\.op\.createHandleFromBinding\(", text, re.M)}:
        lines.append(f"  %mod.ud.sh = call %dx.types.Handle @dx.op.annotateHandle(i32 216, %dx.types.Handle {sh[1]}, "
                     f"%dx.types.ResourceProperties {{ i32 524, i32 {CLOUD_SH_STRIDE} }})")
        rr = "%dx.types.ResRet.f32"
        for e in range(CLOUD_SH_ELEMENTS):
            for q in range(CLOUD_SH_STRIDE // 16):
                n = f"%mod.ud.sh{e}.{q}"
                lines.append(f"  {n} = call {rr} @dx.op.rawBufferLoad.f32(i32 139, %dx.types.Handle %mod.ud.sh, "
                             f"i32 {e}, i32 {16 * q}, i8 15, i32 4)")
                lines += [f"  {n}.{k} = extractvalue {rr} {n}, {k}" for k in range(4)]
                lines.append(f"  call void @dx.op.rawBufferStore.f32(i32 140, %dx.types.Handle %mod.ud.u, "
                             f"i32 {CLOUD_SH_REGION + CLOUD_SH_STRIDE * e + 16 * q}, i32 undef, "
                             + ", ".join(f"float {n}.{k}" for k in range(4)) + ", i8 15, i32 4)")
    lines += ["  br label %mod.udb.end", "", "mod.udb.end:", "  ret void"]
    ir.lines[rets[0]:rets[0] + 1] = lines
    return "User dump" + (", sky light SH" if sh else "")


PATCHES = {"medium": patch_medium, "cloud_ap": patch_cloud_ap, "clouds": patch_clouds,
           "shadow_capture": patch_shadow_capture, "output_capture": patch_output_capture,
           "cloud_gen_capture": patch_cloud_gen_capture}


def disassemble(blob: Path, dxc: Path) -> str:
    out = subprocess.run([str(dxc), "-dumpbin", str(blob)], capture_output=True, text=True)
    if out.returncode != 0:
        raise PatchError(f"dxc -dumpbin failed: {out.stderr.strip()}")
    return out.stdout


def build_dxilasm() -> None:
    from build_addon import find_cmake
    cmake = find_cmake()
    build_dir = ROOT / "build" / "dxilasm-build"
    subprocess.run([str(cmake), "-S", str(ROOT / "tools" / "dxilasm"), "-B", str(build_dir),
                    "-G", "Visual Studio 17 2022", "-A", "x64"], check=True, capture_output=True)
    subprocess.run([str(cmake), "--build", str(build_dir), "--config", "Release"], check=True,
                   capture_output=True)


def patch_all(keep_ir: bool = False, root: Path | None = None, dxc: Path | None = None,
              dxilasm: Path | None = None, strict: bool = True) -> int:
    """Patch every manifest target; returns the number of failures. root holds
    shaders/manifest.json and extracted/ and receives build/ (the project by
    default; the installer passes its work folder, its dxc and dxilasm).
    strict = False: a target the patterns don't recognise (a sim update) is
    reported and skipped, whatever the error."""
    root = root or ROOT
    if dxilasm is None:
        dxilasm = DXILASM
        if not dxilasm.exists():
            build_dxilasm()
    dxc = dxc or find_dxc()
    manifest = json.loads((root / "shaders" / "manifest.json").read_text())
    index = list(csv.DictReader(open(root / "extracted" / "index.csv")))
    failures = 0
    for target in manifest.get("patches", []):
        entry = target["entry"]
        out_dir = root / "build" / ("patched" if target.get("lab_only") else "shaders")
        out_dir.mkdir(parents=True, exist_ok=True)
        blobs = sorted({root / "extracted" / row["path"] for row in index if row["entry"] == entry})
        crcs = target.get("crcs", "all")
        for blob in blobs:
            crc = blob.name.split(".")[0]
            if crcs != "all" and crc not in crcs:
                continue
            try:
                patched, summary = PATCHES[target.get("kind", "medium")](disassemble(blob, dxc),
                                                                         **target.get("args", {}))
            except PatchError as e:
                print(f"{entry} {crc}: NOT PATCHED: {e}")
                failures += 1
                continue
            except Exception as e:  # (only with strict = False)
                if strict:
                    raise
                print(f"{entry} {crc}: NOT PATCHED: {type(e).__name__}: {e}")
                failures += 1
                continue
            ir_dir = root / "build" / "patched" / "ir"
            ir_dir.mkdir(parents=True, exist_ok=True)
            ir_path = ir_dir / f"{crc}.ll"
            ir_path.write_text(patched)
            out = out_dir / f"{crc}.cso"
            res = subprocess.run([str(dxilasm), str(dxc.parent), str(ir_path), str(out)],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                print(f"{entry} {crc}: assembly failed\n{res.stderr}")
                failures += 1
                continue
            if not keep_ir:
                ir_path.unlink()
            print(f"patched {out.relative_to(root)}  <- {entry}: {summary}")
    return failures


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep-ir", action="store_true")
    args = ap.parse_args()
    if failures := patch_all(args.keep_ir):
        raise SystemExit(f"{failures} target(s) failed")


if __name__ == "__main__":
    main()
