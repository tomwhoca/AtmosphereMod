"""Assemble DXIL from LLVM IR text (as dxc -dumpbin prints it) into a
validated, signed shader container, in this process.

    from dxil_assemble import Assembler
    cso = Assembler(dxc_bin_dir).assemble(ir_text)

Uses only dxcompiler.dll and dxil.dll from Microsoft's DirectX Shader Compiler
(the Windows SDK's or the release's), through their COM interfaces: the
optimizer parses the IR text (it accepts text as well as bitcode) and, with
no passes, writes it back as bitcode; the assembler wraps the bitcode in a
container (signatures, PSV0 and feature info are rebuilt from the module
metadata); the validator checks it and signs it in place. This used to be a
small C++ helper (dxilasm.exe), which an antivirus heuristic flagged.
"""

import ctypes
import uuid
from pathlib import Path

HRESULT = ctypes.c_long
PTR = ctypes.c_void_p
OUT = ctypes.POINTER(ctypes.c_void_p)
DXC_CP_ACP = 0
DXC_CP_UTF8 = 65001
VALIDATOR_IN_PLACE_EDIT = 1  # DxcValidatorFlags_InPlaceEdit: sign the blob it's given

# dxcapi.h: classes, interfaces, and the vtable slots we call (IUnknown takes 0-2)
CLSID_DXC_UTILS = "6245d6af-66e0-48fd-80b4-4d271796748c"  # (CLSID_DxcLibrary)
CLSID_DXC_OPTIMIZER = "ae2cd79f-cc22-453f-9b6b-b124e7a5204c"
CLSID_DXC_ASSEMBLER = "d728db68-f903-4f80-94cd-dccf76ec7151"
CLSID_DXC_VALIDATOR = "8ca3e215-f728-4cf3-8cdd-88af917587a1"
IID_UTILS = "4605c4cb-2019-492a-ada4-65f20bb7d67f"
IID_OPTIMIZER = "25740e2e-9cba-401b-9119-4fb42f39f270"
IID_ASSEMBLER = "091f7a26-1c1f-4948-904b-e6e3a8a771d5"
IID_VALIDATOR = "a6e82bd2-1fd7-4826-9811-2857e797f49a"
RELEASE = 2
BLOB_POINTER, BLOB_SIZE = 3, 4                                   # IDxcBlob
RESULT_STATUS, RESULT_BLOB, RESULT_ERRORS = 3, 4, 5              # IDxcOperationResult
UTILS_CREATE_BLOB = 6                                            # IDxcUtils::CreateBlob (copies)
OPTIMIZER_RUN = 5                                                # IDxcOptimizer::RunOptimizer
ASSEMBLER_ASSEMBLE = 3                                           # IDxcAssembler::AssembleToContainer
VALIDATOR_VALIDATE = 3                                           # IDxcValidator::Validate


class AssemblyError(Exception):
    pass


class GUID(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]


def guid(text: str) -> GUID:
    g = GUID()
    g.bytes[:] = uuid.UUID(text).bytes_le
    return g


class Com:
    """A COM interface pointer, released on exit."""

    def __init__(self, ptr: ctypes.c_void_p):
        self.ptr = ptr

    def call(self, slot: int, restype, argtypes, *args):
        vtable = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(restype, PTR, *argtypes)(vtable[slot])(self.ptr, *args)

    def bytes(self) -> bytes:  # (an IDxcBlob)
        return ctypes.string_at(self.call(BLOB_POINTER, PTR, ()), self.call(BLOB_SIZE, ctypes.c_size_t, ()))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.ptr:
            self.call(RELEASE, ctypes.c_ulong, ())
            self.ptr = None


def out_com(fn, what: str) -> Com:
    """Calls fn(out pointer) and wraps what it returns."""
    out = ctypes.c_void_p()
    hr = fn(ctypes.byref(out))
    if hr < 0 or not out:
        raise AssemblyError(f"{what} failed (0x{hr & 0xFFFFFFFF:08x})")
    return Com(out)


def check(result: Com, what: str) -> None:
    """Raises with the error text if an IDxcOperationResult failed."""
    status = HRESULT()
    result.call(RESULT_STATUS, HRESULT, (ctypes.POINTER(HRESULT),), ctypes.byref(status))
    if status.value < 0:
        errors = ctypes.c_void_p()
        text = ""
        if result.call(RESULT_ERRORS, HRESULT, (OUT,), ctypes.byref(errors)) >= 0 and errors:
            with Com(errors) as e:
                text = e.bytes().decode("utf-8", "replace").strip()
        raise AssemblyError(f"{what} failed (0x{status.value & 0xFFFFFFFF:08x})" + (f":\n{text}" if text else ""))


class Assembler:
    def __init__(self, bin_dir: Path):
        bin_dir = Path(bin_dir)
        # dxcompiler.dll looks for dxil.dll next to itself when validating.
        ctypes.windll.kernel32.SetDllDirectoryW(str(bin_dir))
        try:
            self._dlls = [ctypes.WinDLL(str(bin_dir / n)) for n in ("dxcompiler.dll", "dxil.dll")]
        except OSError as e:
            raise AssemblyError(f"cannot load dxcompiler.dll / dxil.dll from {bin_dir}: {e}") from e
        compiler, validator = self._dlls
        self.utils = self._create(compiler, CLSID_DXC_UTILS, IID_UTILS)
        self.optimizer = self._create(compiler, CLSID_DXC_OPTIMIZER, IID_OPTIMIZER)
        self.assembler = self._create(compiler, CLSID_DXC_ASSEMBLER, IID_ASSEMBLER)
        self.validator = self._create(validator, CLSID_DXC_VALIDATOR, IID_VALIDATOR)

    @staticmethod
    def _create(dll, clsid: str, iid: str) -> Com:
        create = dll.DxcCreateInstance
        create.restype = HRESULT
        create.argtypes = (ctypes.POINTER(GUID), ctypes.POINTER(GUID), OUT)
        c, i = guid(clsid), guid(iid)
        return out_com(lambda p: create(ctypes.byref(c), ctypes.byref(i), p), f"creating {clsid}")

    def _blob(self, data: ctypes.c_void_p, size: int, code_page: int) -> Com:
        return out_com(lambda p: self.utils.call(UTILS_CREATE_BLOB, HRESULT, (PTR, ctypes.c_uint32, ctypes.c_uint32, OUT),
                                                 data, size, code_page, p), "creating a blob")

    def assemble(self, ir_text: str) -> bytes:
        text = ir_text.encode("utf-8")
        buf = ctypes.create_string_buffer(text, len(text) + 1)  # (the IR parser wants it terminated)
        with self._blob(ctypes.cast(buf, PTR), len(text), DXC_CP_UTF8) as source:
            # Text -> bitcode (no passes).
            unused_text = ctypes.c_void_p()
            with out_com(lambda p: self.optimizer.call(OPTIMIZER_RUN, HRESULT, (PTR, PTR, ctypes.c_uint32, OUT, OUT),
                                                       source.ptr, None, 0, p, ctypes.byref(unused_text)),
                         "parsing the IR") as bitcode:
                if unused_text:
                    Com(unused_text).__exit__()
                # Bitcode -> container.
                with out_com(lambda p: self.assembler.call(ASSEMBLER_ASSEMBLE, HRESULT, (PTR, OUT), bitcode.ptr, p),
                             "assembly") as assembled:
                    check(assembled, "assembly")
                    with out_com(lambda p: assembled.call(RESULT_BLOB, HRESULT, (OUT,), p),
                                 "getting the container") as container:
                        # Validate and sign. The validator writes the hash into
                        # the blob, so give it a private, writable copy.
                        pointer = container.call(BLOB_POINTER, PTR, ())
                        size = container.call(BLOB_SIZE, ctypes.c_size_t, ())
                        with self._blob(pointer, size, DXC_CP_ACP) as signed:
                            with out_com(lambda p: self.validator.call(VALIDATOR_VALIDATE, HRESULT,
                                                                       (PTR, ctypes.c_uint32, OUT), signed.ptr,
                                                                       VALIDATOR_IN_PLACE_EDIT, p),
                                         "validation") as validated:
                                check(validated, "validation")
                            return signed.bytes()
