// Active Sky as a weather engine, through the local web API it serves while
// running (http://localhost:19285/ActiveSky/API/).

#pragma once

#include "weather_engine.h"

#include <cstdint>
#include <utility>

namespace activesky
{
class ActiveSky final : public engine::WeatherEngine
{
public:
    explicit ActiveSky(engine::Log log) : log_(std::move(log)) {}
    const char *name() const override { return "Active Sky"; }
    bool poll() override;
    bool injecting() const override { return state_ == State::injecting; }
    std::optional<haze::Metar> depict(const haze::Metar &metar) override;

private:
    enum class State { absent, idle, injecting };
    engine::Log log_;
    State state_ = State::absent;
    uint64_t next_poll_ms_ = 0;
    std::string undepicted_logged_;  // the last station it couldn't depict, logged
};
} // namespace activesky
