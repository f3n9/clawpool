#!/usr/bin/env python3
import os


def choose_resource_profile(active_instances, boost_threshold):
    if int(active_instances) < int(boost_threshold):
        return "boost"
    return "base"


def main():
    threshold = int(os.getenv("OPENCLAW_BOOST_THRESHOLD", "10"))
    print(f"resource-controller running with boost-threshold={threshold}")


if __name__ == "__main__":
    main()
