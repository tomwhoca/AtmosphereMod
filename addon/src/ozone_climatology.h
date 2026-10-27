// Total ozone climatology: the ozone column over a place in a given season,
// for the fitted sky colours (T8). The amount of ozone sets how blue the sky
// stays at dusk; it ranges from ~220 DU (Antarctic spring) through ~260 DU
// (tropics) to ~440 DU (Arctic spring).
//
// Data: Fortuin & Kelder (1998), "An ozone climatology based on ozonesonde
// and satellite measurements", J. Geophys. Res. 103, 31709-31734,
// doi:10.1029/1998JD200008; total columns as published by KNMI's TEMIS
// (temis.nl/data/fortuin.html, o3column.dat), rounded to 0.1 DU. Monthly
// means of 1980-1991 in 17 bands of 10 degrees, centred on -80..+80; from
// sondes as well as satellites, so the polar night is covered.

#pragma once

#include <algorithm>
#include <cmath>

namespace ozone
{
constexpr double kReferenceDu = 300.0;  // the column the fitted sky colours assume

// [month][band]: January..December, bands centred on -80, -70, ..., +80 degrees.
constexpr float kColumnDu[12][17] = {
    { 302.5f, 317.4f, 324.8f, 292.1f, 294.2f, 275.7f, 264.9f, 260.9f, 251.8f, 258.0f, 249.7f, 277.8f, 333.1f, 375.5f, 384.8f, 376.4f, 371.8f },
    { 292.1f, 306.3f, 314.9f, 288.6f, 286.7f, 270.2f, 262.2f, 259.9f, 249.7f, 262.2f, 266.9f, 287.7f, 350.4f, 396.7f, 411.1f, 412.1f, 419.4f },
    { 287.9f, 297.7f, 306.0f, 292.9f, 279.4f, 271.1f, 259.4f, 258.3f, 255.0f, 266.7f, 264.7f, 301.4f, 356.5f, 404.4f, 428.0f, 440.7f, 438.7f },
    { 281.4f, 299.7f, 306.6f, 295.4f, 283.7f, 268.6f, 260.3f, 258.4f, 260.0f, 276.8f, 276.9f, 308.3f, 357.1f, 391.1f, 416.6f, 438.9f, 440.7f },
    { 279.1f, 279.9f, 308.8f, 303.2f, 291.9f, 271.9f, 259.2f, 256.2f, 240.9f, 282.2f, 284.9f, 310.2f, 349.3f, 378.5f, 392.1f, 402.0f, 401.3f },
    { 282.9f, 294.5f, 319.4f, 314.5f, 305.7f, 281.0f, 260.8f, 255.2f, 260.7f, 284.5f, 283.5f, 298.5f, 331.7f, 359.9f, 361.2f, 358.2f, 364.2f },
    { 283.7f, 292.0f, 323.8f, 324.7f, 321.4f, 289.9f, 266.6f, 257.9f, 264.0f, 283.6f, 282.6f, 289.8f, 313.5f, 336.6f, 337.4f, 327.1f, 325.0f },
    { 273.3f, 292.8f, 324.6f, 344.7f, 339.0f, 299.9f, 272.0f, 262.0f, 268.9f, 282.5f, 280.3f, 284.1f, 302.7f, 318.5f, 319.5f, 307.1f, 297.0f },
    { 224.9f, 260.4f, 333.0f, 359.7f, 347.2f, 309.0f, 290.7f, 269.5f, 271.9f, 279.8f, 274.5f, 280.2f, 293.8f, 308.2f, 309.2f, 297.0f, 286.4f },
    { 217.1f, 278.6f, 350.2f, 364.5f, 373.5f, 310.8f, 283.2f, 270.1f, 267.7f, 275.8f, 269.0f, 273.1f, 286.7f, 305.4f, 311.1f, 306.4f, 296.8f },
    { 292.2f, 327.9f, 353.4f, 344.4f, 349.4f, 295.9f, 276.5f, 265.2f, 258.1f, 264.6f, 256.3f, 265.0f, 293.5f, 318.8f, 323.9f, 346.7f, 287.9f },
    { 328.2f, 335.9f, 336.6f, 323.1f, 304.4f, 283.1f, 269.0f, 262.7f, 251.5f, 257.2f, 250.8f, 269.3f, 311.7f, 341.9f, 349.7f, 343.1f, 327.6f },
};

// The column (DU) at a latitude (degrees) and day of the year (1 = 1 January),
// bilinear between band centres and mid-months (cyclic over the year).
inline double column_du(double lat_deg, double day_of_year)
{
    const double band = std::clamp((lat_deg + 80.0) / 10.0, 0.0, 16.0);
    const int b0 = std::min(static_cast<int>(band), 15);
    const double fb = band - b0;
    double month = (day_of_year - 0.5) / (365.25 / 12.0) - 0.5;  // 0 = mid-January
    month -= 12.0 * std::floor(month / 12.0);
    const int m0 = static_cast<int>(month) % 12, m1 = (m0 + 1) % 12;
    const double fm = month - std::floor(month);
    const auto at = [&](int m) { return kColumnDu[m][b0] * (1.0 - fb) + kColumnDu[m][b0 + 1] * fb; };
    return at(m0) * (1.0 - fm) + at(m1) * fm;
}
} // namespace ozone
