// Standalone WARP diagnosis. Does not load the game, inject, or change a profile.
#include "PreparationPlanner.h"
#include "SnapshotIO.h"
#include <wrl/client.h>
#include <objbase.h>
#include <cassert>
#include <iostream>
using namespace ModLoading;
using Microsoft::WRL::ComPtr;

void CheckCanceledCompletion(ID3D11Device* device) {
    PreparationPlanner planner(1024 * 1024, 32);
    assert(planner.BeginDevice(device, AllocateDeviceEpoch()));
    planner.SetSafetyHeadroom(0);
    GpuPreparationRequest request;
    request.snapshot.bytes = std::make_shared<const std::vector<uint8_t>>(16, 1);
    request.snapshot.digest = HashBytes(request.snapshot.bytes->data(), 16);
    request.buffer.ByteWidth = 16; request.buffer.Usage = D3D11_USAGE_DEFAULT;
    request.buffer.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    request.estimatedBytes = 65552; request.dependencyReadOnly = true;
    unsigned completed = 0;
    request.onReady = [&](const DeviceResourceKey&, const std::shared_ptr<PreparedGpuObject>&) { ++completed; };
    assert(planner.Enqueue(request) == GpuQueueResult::Queued);
    assert(!planner.Pump(false).created && completed == 0);
    planner.CancelPending();
    assert(!planner.Pump(true).created && completed == 0);
    assert(planner.Enqueue(request) == GpuQueueResult::Queued);
    planner.RetireDevice();
    assert(completed == 0);
}

void ProbePinTiming(ID3D11Device* device, bool pinAfterPump) {
    // Reproduce Runtime.cpp's eight-declaration scan followed by GPU Pump.
    // The full working set deliberately exceeds this small cache.
    constexpr uint64_t estimate = 65552;
    PreparationPlanner planner(16 * estimate, 32);
    assert(planner.BeginDevice(device, AllocateDeviceEpoch()));
    planner.SetSafetyHeadroom(0);
    GpuPreparationRequest request;
    request.snapshot.bytes = std::make_shared<const std::vector<uint8_t>>(16, 1);
    request.snapshot.digest = HashBytes(request.snapshot.bytes->data(), 16);
    request.buffer.ByteWidth = 16; request.buffer.Usage = D3D11_USAGE_DEFAULT;
    request.buffer.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    request.estimatedBytes = estimate; request.dependencyReadOnly = true;
    std::vector<std::shared_ptr<PreparedGpuObject>> pins(64);
    std::vector<DeviceResourceKey> keys(64);
    GpuPumpLimits limits; limits.operations = 16; limits.bytes = 64 * estimate; limits.milliseconds = 1000;
    size_t created = 0, retained = 0;
    for (size_t batch = 0; batch < 16; ++batch) {
        for (size_t k = 0; k < 8; ++k) {
            const auto i = (batch * 8 + k) % 64;
            request.creationOptions = {0x50524956, uint32_t(i)};
            request.onReady = {};
            if (pinAfterPump) request.onReady = [&, i](const DeviceResourceKey& key, const std::shared_ptr<PreparedGpuObject>& object) {
                assert(!(key < keys[i]) && !(keys[i] < key));
                pins[i] = object;
            };
            if (!pins[i]) planner.Enqueue(request, &keys[i]);
            if (!pins[i]) pins[i] = planner.TryAcquire(keys[i]);
        }
        created += planner.Pump(true, limits).created;
    }
    for (const auto& pin : pins) if (pin) ++retained;
    assert(pinAfterPump ? (created == 16 && retained == 16) : (created == 128 && retained == 0));
    std::cout << ",\"" << (pinAfterPump ? "immediate_pins" : "delayed_pins")
        << "\":{\"creation_calls\":" << created << ",\"retained\":" << retained << "}";
    pins.clear(); planner.RetireDevice();
}

int main() {
    assert(SUCCEEDED(CoInitializeEx(nullptr, COINIT_MULTITHREADED)));
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    assert(SUCCEEDED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0,
        nullptr, 0, D3D11_SDK_VERSION, device.GetAddressOf(), nullptr, context.GetAddressOf())));
    constexpr uint64_t capacity = 64ull * 1024 * 1024;
    PreparationPlanner planner(capacity, 32);
    assert(planner.BeginDevice(device.Get(), AllocateDeviceEpoch()));
    planner.SetSafetyHeadroom(0);
    std::vector<uint8_t> bytes(144, 0);
    const auto put = [&](size_t p, uint32_t v) {
        for (size_t i = 0; i < 4; ++i) bytes[p + i] = uint8_t(v >> (8 * i));
    };
    put(0, 0x20534444); put(4, 124); put(8, 0xA1007);
    put(12, 4); put(16, 4); put(20, 8); put(28, 2);
    put(76, 32); put(80, 4); put(84, 0x31545844); put(108, 0x401008);
    GpuPreparationRequest request;
    request.snapshot.bytes = std::make_shared<const std::vector<uint8_t>>(bytes);
    request.snapshot.digest = HashBytes(bytes.data(), bytes.size());
    request.kind = GpuPreparationKind::EncodedDDS;
    request.dependencyReadOnly = true;
    request.createDefaultShaderResourceView = true;
    request.estimatedBytes = EstimateEncodedTextureBytes(request.snapshot, request.kind) + 65536;
    request.reserveCpuScratch = [](uint64_t) { return std::make_shared<int>(0); };
    GpuPumpLimits limits; limits.operations = 1; limits.bytes = capacity; limits.milliseconds = 1000;
    unsigned full = 0, budget = 0, other = 0;
    std::vector<std::shared_ptr<PreparedGpuObject>> pins;
    std::vector<DeviceResourceKey> keys;
    for (uint32_t i = 0; i < 256; ++i) {
        request.creationOptions = {0x50524956, i};
        DeviceResourceKey key;
        const auto result = planner.Enqueue(request, &key);
        if (result == GpuQueueResult::Full) { ++full; continue; }
        if (result == GpuQueueResult::BudgetBlocked) { ++budget; continue; }
        if (result != GpuQueueResult::Queued) { ++other; continue; }
        const auto pump = planner.Pump(true, limits);
        assert(pump.created == 1 && !pump.failed && !pump.deviceRemoved);
        auto object = planner.TryAcquire(key); assert(object && object->shaderResourceView);
        D3D11_TEXTURE2D_DESC desc{};
        static_cast<ID3D11Texture2D*>(object->resource)->GetDesc(&desc);
        assert(desc.Width == 4 && desc.Height == 4 && desc.MipLevels == 2 && desc.Format == DXGI_FORMAT_BC1_UNORM);
        if (!pins.empty()) assert(object->resource != pins.back()->resource);
        keys.push_back(key); pins.push_back(object);
    }
    for (size_t i = 0; i < keys.size(); ++i) assert(planner.TryAcquire(keys[i]) == pins[i]);
    assert(!planner.Queued());
    // Retained texture keys have a separate bound from the 32-slot work queue.
    assert(pins.size() == 256 && !full && !budget && !other);
    std::cout << "{\"attempts\":256,\"created_and_reused\":" << pins.size()
        << ",\"full\":" << full << ",\"budget_blocked\":" << budget
        << ",\"other\":" << other << ",\"cache_cap_bytes\":" << capacity
        << ",\"charged_bytes\":" << pins.size() * request.estimatedBytes;
    pins.clear(); planner.RetireDevice();
    ProbePinTiming(device.Get(), false);
    ProbePinTiming(device.Get(), true);
    CheckCanceledCompletion(device.Get());
    std::cout << "}\n";
    context.Reset(); device.Reset();
    CoUninitialize();
}
