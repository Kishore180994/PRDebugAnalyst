#!/bin/bash
# Cleanup old build artifacts
find . -name "*.class" -delete
find . -name "build" -type d -exec rm -rf {} +
