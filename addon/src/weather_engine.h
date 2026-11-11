// A third-party weather engine that injects its own weather into the sim.
// While it does, its depicted METARs describe what the sim shows, so the
// weather feed uses them instead of the sim's (weather_feed.cpp).

#pragma once

#include "haze_mapping.h"

#include <functional>
#include <optional>
#include <string>

namespace engine
{
using Log = std::function<void(const std::string &)>;

class WeatherEngine
{
public:
    virtual ~WeatherEngine() = default;
    virtual const char *name() const = 0;
    // Called every loop of the weather thread; checks on the engine at its own
    // pace. True when it started or stopped feeding the sim.
    virtual bool poll() = 0;
    virtual bool injecting() const = 0;
    // The engine's depicted METAR at `metar`'s station; none when it has none.
    virtual std::optional<haze::Metar> depict(const haze::Metar &metar) = 0;
};
} // namespace engine
