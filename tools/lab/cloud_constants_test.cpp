// The cloud constants as the add-on computes them (addon/src/cloud_constants.h), for tools/test_cloud_constants.py:
// reads lines of C0..C35 from stdin, prints each line's constants (%.9g, space separated).

#include "cloud_constants.h"

#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>

int main()
{
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        float c[cloud_constants::kTunables] = {};
        for (float &v : c)
            in >> v;
        float out[cloud_constants::kCount] = {};
        cloud_constants::compute(c, out);
        for (uint32_t k = 0; k < cloud_constants::kCount; ++k)
            std::printf(k ? " %.9g" : "%.9g", out[k]);
        std::printf("\n");
    }
    return 0;
}
