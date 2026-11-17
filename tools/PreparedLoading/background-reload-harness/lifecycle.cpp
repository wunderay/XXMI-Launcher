// Mock lifecycle harness: the exact pacing constant, slot/pump/drain code, RuntimeOwnerScope and
// RetireRuntimeDevice from the patched sources (extracted by tests/test_background_reload_patch.py),
// compiled against mock graph/device/runtime types. Not a D3D test.
#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

struct ID3D11Device {};
struct CustomResource { int id = 0; };
struct CustomShader { int id = 0; };
struct ConfigurationState {
	std::map<int, CustomResource> resources;
	std::map<int, CustomShader> shaders;
	std::function<void()> onDestroy;
	~ConfigurationState() { if (onDestroy) onDestroy(); }
};
using ConfigGenerationPin = std::shared_ptr<ConfigurationState>;

static std::function<void()> releaseHook;
static int childReleases = 0;

namespace {
void ReleaseResourceChildren(CustomResource&) { ++childReleases; if (releaseHook) releaseHook(); }
void ReleaseShaderChildren(CustomShader&) { ++childReleases; }
#include EXACT
} // namespace

// Mock runtime state for the exact RetireRuntimeDevice
enum class PreparationMode { Off, On };
enum class PreparationStatusState { Off, Preparing };
struct PreparationStatus { PreparationMode mode = PreparationMode::Off; size_t totalAssets = 0;
	PreparationStatusState state = PreparationStatusState::Off; std::string reason; };
struct RuntimeState { PreparationMode mode = PreparationMode::On; size_t totalAssets = 0; };
struct Activation { void SetReady(int) {} };
struct Service {
	std::atomic<bool> retirementRequested{false};
	std::recursive_mutex plannerMutex;
	std::shared_ptr<int> planner, refreshPlanner, pendingRefresh, pendingGeneral;
	std::vector<int> profileGpuPins, activeResources;
	Activation refreshActivation;
	ID3D11Device* device = nullptr; int epoch = 0; int plannerGeneration = 0;
};
static Service& Services() { static Service s; return s; }
static void InvalidatePreparationStatus() {}
static int ReservePreparationStatusTicket() { return 1; }
static void UpdatePreparationStatus(const PreparationStatus&, int) {}
static std::shared_ptr<RuntimeState> Current() { return std::make_shared<RuntimeState>(); }
static thread_local bool runtimeBusy = false;
static thread_local bool retiringDevice = false;
void RetireRuntimeDevice(ID3D11Device* device);
#include OWNER
#include RETIRE

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

static std::shared_ptr<ConfigGenerationPin> MakeGraph(int resources, int* destroyed) {
	auto state = std::make_shared<ConfigurationState>();
	for (int i = 0; i < resources; ++i) state->resources[i] = CustomResource{i};
	state->onDestroy = [destroyed] { ++*destroyed; };
	return std::make_shared<ConfigGenerationPin>(std::move(state));
}
static void Queue(std::shared_ptr<ConfigGenerationPin> graph, ID3D11Device* device) {
	std::lock_guard<std::mutex> lock(IncrementalReleaseMutex());
	for (auto& slot : IncrementalReleaseSlots()) {
		if (slot.graph) continue;
		slot.graph = std::move(graph); slot.device = device; slot.owner = std::this_thread::get_id(); slot.started = false;
		return;
	}
}
static int Occupied() { int n = 0; for (auto& s : IncrementalReleaseSlots()) n += s.graph != nullptr; return n; }

int main(int argc, char**) {
	ID3D11Device device;
	if (argc > 1) {
		// Direct drain from a Release() inside the pump (same thread holds the mutex)
		int destroyed = 0; int calls = 0;
		Queue(MakeGraph(40, &destroyed), &device);
		releaseHook = [&] { if (calls++ == 0) DrainIncrementalRelease(); };
		PumpIncrementalRelease(&device);
		releaseHook = nullptr;
		std::printf("direct reentrant drain: deferred=%d\n", (int)IncrementalDrainRequested().load());
		return 0;
	}
	// 1. Retirement with no other preparation work (the early-return path) frees every slot
	int destroyed = 0;
	for (int i = 0; i < 4; ++i) Queue(MakeGraph(40, &destroyed), &device);
	CHECK(Occupied() == 4);
	RetireRuntimeDevice(&device);
	std::printf("early-return retirement: occupied=%d destroyed=%d\n", Occupied(), destroyed);
	CHECK(Occupied() == 0 && destroyed == 4);

	// 2. A Release() inside the pump that retires the device on the same thread
	destroyed = 0; int reentries = 0;
	for (int i = 0; i < 2; ++i) Queue(MakeGraph(40, &destroyed), &device);
	releaseHook = [&] { if (reentries++ == 0) RetireRuntimeDevice(&device); };
	PumpIncrementalRelease(&device);
	releaseHook = nullptr;
	const bool deferred = IncrementalDrainRequested().load();
	std::printf("reentrant drain: deferred=%d occupied=%d\n", deferred, Occupied());
	CHECK(deferred);
	PumpIncrementalRelease(&device); // the next pump takes every slot first
	std::printf("after next pump: occupied=%d destroyed=%d\n", Occupied(), destroyed);
	CHECK(Occupied() == 0 && destroyed == 2);

	// 3. The table is reusable afterwards; release is paced at the patch's per-frame budget
	destroyed = 0; childReleases = 0;
	Queue(MakeGraph(40, &destroyed), &device);
	PumpIncrementalRelease(&device);
	const int firstFrame = childReleases;
	for (int i = 0; i < 10 && Occupied(); ++i) PumpIncrementalRelease(&device);
	std::printf("paced release: first frame=%d child releases=%d destroyed=%d\n", firstFrame, childReleases, destroyed);
	CHECK(firstFrame == 16 && childReleases == 40 && destroyed == 1);

	// 4. Retirement deferred to RuntimeOwnerScope: requested on the thread inside the scope
	destroyed = 0;
	for (int i = 0; i < 4; ++i) Queue(MakeGraph(8, &destroyed), &device);
	{
		std::unique_lock<std::recursive_mutex> lock(Services().plannerMutex);
		RuntimeOwnerScope scope(lock);
		RetireRuntimeDevice(&device); // runtimeBusy: deferred to the scope
		CHECK(Occupied() == 4);
	}
	std::printf("deferred retirement, same thread: occupied=%d destroyed=%d\n", Occupied(), destroyed);
	CHECK(Occupied() == 0 && destroyed == 4);

	// 5. ... and requested from another thread while the scope holds the planner lock
	destroyed = 0;
	for (int i = 0; i < 4; ++i) Queue(MakeGraph(8, &destroyed), &device);
	{
		std::unique_lock<std::recursive_mutex> lock(Services().plannerMutex);
		RuntimeOwnerScope scope(lock);
		std::thread([&] { RetireRuntimeDevice(&device); }).join(); // try_lock fails: deferred
		CHECK(Occupied() == 4);
	}
	std::printf("deferred retirement, other thread: occupied=%d destroyed=%d\n", Occupied(), destroyed);
	CHECK(Occupied() == 0 && destroyed == 4);

	// 6. A replacement device presented from another thread still reaches the cleanup
	destroyed = 0;
	ID3D11Device replacement;
	for (int i = 0; i < 2; ++i) Queue(MakeGraph(40, &destroyed), &device);
	std::thread([&] { PumpIncrementalRelease(&replacement); PumpIncrementalRelease(&replacement); }).join();
	std::printf("replacement device, other thread: occupied=%d destroyed=%d\n", Occupied(), destroyed);
	CHECK(Occupied() == 0 && destroyed == 2);
	std::printf(failures ? "FAILED\n" : "OK\n");
	return failures ? 1 : 0;
}
