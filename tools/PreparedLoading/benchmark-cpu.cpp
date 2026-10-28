// Read-only component benchmark against real mod assets. Never opens a game.
// This measures CPU snapshot preparation/cache access, not GPU or game latency.
#include "PreparationScheduler.h"
#include "SnapshotIO.h"
#include <Windows.h>
#include <atomic>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <cstdio>
using namespace ModLoading;
using Clock = std::chrono::steady_clock;
static double Milliseconds(Clock::time_point begin) {
    return std::chrono::duration<double, std::milli>(Clock::now()-begin).count();
}
int wmain(int argc, wchar_t** argv) {
    try {
        if (argc < 4) return 2;
        const bool bounded = std::wstring(argv[1]) == L"bounded";
        const size_t maximum = 4 * 1024 * 1024;
        PreparationLimits limits; limits.cpuBytes=16*1024*1024;
        limits.maxAssetBytes=maximum; limits.workers=1; limits.maxQueued=2;
        std::atomic<unsigned> reads{0}; std::atomic<uint64_t> bytes{0};
        PreparationScheduler scheduler(limits, [&](const std::wstring& path,size_t size,SourceSnapshot& out,std::string& error) {
            ++reads; const bool ok=ReadVerifiedSnapshot(path,size,out,error);
            if(ok) bytes += out.bytes->size(); return ok;
        });
        const auto started=Clock::now();
        unsigned accepted=0, rejected=0;
        for (unsigned pass=0;pass<3;++pass) for(int i=2;i<argc;++i) {
            bool queued;
#ifdef LEGACY_SCHEDULER
            queued=scheduler.Enqueue(argv[i],1);
#else
            queued=scheduler.Enqueue(argv[i],1,0,bounded);
#endif
            if(!queued) { ++rejected; continue; }
            ++accepted;
            const auto deadline=Clock::now()+std::chrono::seconds(10);
            while(!scheduler.TryGet(argv[i],1) && Clock::now()<deadline && scheduler.CompatibilityReason(argv[i],1).empty())
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            if(!scheduler.TryGet(argv[i],1)) throw std::runtime_error("asset did not become ready");
        }
        const double preparation=Milliseconds(started);
        // Compare equivalent resident bytes against ordinary file read/allocation.
        unsigned residentFiles=0; uint64_t residentBytes=0;
        double directMs=0, cachedMs=0; uint64_t checksum=0;
        for(int i=2;i<argc;++i) {
            auto asset=scheduler.TryGet(argv[i],1); if(!asset) continue;
            ++residentFiles; residentBytes+=asset->snapshot.bytes->size();
            for(unsigned round=0;round<9;++round) {
                auto direct=[&] {
                    const auto begin=Clock::now();
                    FILE* stream=nullptr; if(_wfopen_s(&stream,argv[i],L"rb") || !stream) throw std::runtime_error("read open");
                    std::vector<uint8_t> data(asset->snapshot.bytes->size());
                    const auto got=fread(data.data(),1,data.size(),stream); fclose(stream);
                    directMs+=Milliseconds(begin);
                    if(got!=data.size() || data!=*asset->snapshot.bytes) throw std::runtime_error("byte mismatch");
                    checksum+=data.front();
                };
                auto cached=[&] {
                    const auto begin=Clock::now(); auto hit=scheduler.TryGet(argv[i],1);
                    cachedMs+=Milliseconds(begin);
                    if(!hit || hit->snapshot.digest!=asset->snapshot.digest) throw std::runtime_error("cache mismatch");
                    checksum+=hit->snapshot.bytes->front();
                };
                if(round%2) { cached(); direct(); } else { direct(); cached(); }
            }
        }
        scheduler.Stop();
        std::cout << "{\"bounded\":" << (bounded?"true":"false")
            << ",\"files\":" << argc-2 << ",\"passes\":3,\"reader_calls\":" << reads
            << ",\"read_bytes\":" << bytes << ",\"accepted\":" << accepted << ",\"rejected\":" << rejected
            << ",\"preparation_wall_ms\":" << preparation << ",\"resident_files\":" << residentFiles
            << ",\"resident_bytes\":" << residentBytes << ",\"comparison_rounds\":9"
            << ",\"ordinary_file_read_total_ms\":" << directMs << ",\"cache_lookup_total_ms\":" << cachedMs
            << ",\"checksum\":" << checksum << "}\n";
        return residentFiles ? 0 : 3;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
