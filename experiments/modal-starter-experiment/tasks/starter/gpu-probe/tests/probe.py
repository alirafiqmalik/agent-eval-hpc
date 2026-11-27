"""Check the sandbox GPU with the CUDA driver API and one kernel launch.

The probe uses only libcuda, which Modal adds to GPU sandboxes, so the image
needs no CUDA toolkit. The kernel computes c = a * b + c on 2**20 floats.
"""

from __future__ import annotations

import array
import ctypes
import json
import subprocess
import sys

KERNEL = rb"""
.version 7.0
.target sm_52
.address_size 64
.visible .entry fma(.param .u64 a, .param .u64 b, .param .u64 c, .param .u32 n)
{
    .reg .pred %p<2>;
    .reg .b32 %r<6>;
    .reg .f32 %f<5>;
    .reg .b64 %rd<11>;
    ld.param.u64 %rd1, [a];
    ld.param.u64 %rd2, [b];
    ld.param.u64 %rd3, [c];
    ld.param.u32 %r2, [n];
    mov.u32 %r3, %ctaid.x;
    mov.u32 %r4, %ntid.x;
    mov.u32 %r5, %tid.x;
    mad.lo.s32 %r1, %r3, %r4, %r5;
    setp.ge.u32 %p1, %r1, %r2;
    @%p1 bra DONE;
    cvta.to.global.u64 %rd4, %rd1;
    cvta.to.global.u64 %rd5, %rd2;
    cvta.to.global.u64 %rd6, %rd3;
    mul.wide.u32 %rd7, %r1, 4;
    add.s64 %rd8, %rd4, %rd7;
    add.s64 %rd9, %rd5, %rd7;
    add.s64 %rd10, %rd6, %rd7;
    ld.global.f32 %f1, [%rd8];
    ld.global.f32 %f2, [%rd9];
    ld.global.f32 %f3, [%rd10];
    fma.rn.f32 %f4, %f1, %f2, %f3;
    st.global.f32 [%rd10], %f4;
DONE:
    ret;
}
""" + b"\0"


def _check(cuda, name: str, *args) -> None:
    status = getattr(cuda, name)(*args)
    if status != 0:
        raise RuntimeError(f"{name} failed with CUDA error {status}")


def probe(names: list[str], memory_mb: int, tolerance_mb: int) -> dict:
    smi = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    cuda = ctypes.CDLL("libcuda.so.1")
    count, device, context, module, function = (ctypes.c_int(), ctypes.c_int(),
                                                ctypes.c_void_p(), ctypes.c_void_p(),
                                                ctypes.c_void_p())
    _check(cuda, "cuInit", 0)
    _check(cuda, "cuDeviceGetCount", ctypes.byref(count))
    _check(cuda, "cuDeviceGet", ctypes.byref(device), 0)
    raw_name = ctypes.create_string_buffer(256)
    _check(cuda, "cuDeviceGetName", raw_name, 256, device)
    total = ctypes.c_size_t()
    _check(cuda, "cuDeviceTotalMem_v2", ctypes.byref(total), device)
    _check(cuda, "cuCtxCreate_v2", ctypes.byref(context), 0, device)
    _check(cuda, "cuModuleLoadData", ctypes.byref(module), ctypes.c_char_p(KERNEL))
    _check(cuda, "cuModuleGetFunction", ctypes.byref(function), module, b"fma")
    n = 1 << 20
    size = n * 4
    host = [array.array("f", [float(i % 97) for i in range(n)]),
            array.array("f", [2.0] * n), array.array("f", [1.0] * n)]
    pointers = [ctypes.c_uint64() for _ in host]
    for pointer, values in zip(pointers, host, strict=True):
        _check(cuda, "cuMemAlloc_v2", ctypes.byref(pointer), ctypes.c_size_t(size))
        _check(
            cuda, "cuMemcpyHtoD_v2", pointer, ctypes.c_void_p(values.buffer_info()[0]),
            ctypes.c_size_t(size),
        )
    length = ctypes.c_uint32(n)
    arguments = (ctypes.c_void_p * 4)(*[ctypes.addressof(p) for p in pointers],
                                       ctypes.addressof(length))
    threads = 256
    grid, one, block = (ctypes.c_uint((n + threads - 1) // threads), ctypes.c_uint(1),
                        ctypes.c_uint(threads))
    _check(cuda, "cuLaunchKernel", function, grid, one, one, block, one, one,
           ctypes.c_uint(0), None, arguments, None)
    _check(cuda, "cuCtxSynchronize")
    result = array.array("f", [0.0] * n)
    _check(
        cuda, "cuMemcpyDtoH_v2", ctypes.c_void_p(result.buffer_info()[0]), pointers[2],
        ctypes.c_size_t(size),
    )
    expected = [a * 2.0 + 1.0 for a in host[0]]
    kernel_ok = result.tolist() == expected
    name = raw_name.value.decode()
    smi_name, _, smi_memory = smi[0].rpartition(",")
    report = {
        "nvidia_smi": smi,
        "device_count": count.value,
        "device_name": name,
        "driver_memory_mb": total.value // (1024 * 1024),
        "nvidia_smi_memory_mb": int(smi_memory),
        "kernel_elements": n,
        "kernel_ok": kernel_ok,
    }
    report["passed"] = (
        count.value >= 1
        and kernel_ok
        and name in names
        and smi_name.strip() == name
        and abs(int(smi_memory) - memory_mb) <= tolerance_mb
    )
    return report


if __name__ == "__main__":
    config = json.loads(open(sys.argv[1]).read())
    report = probe(config["names"], config["memory_mb"], config["tolerance_mb"])
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
