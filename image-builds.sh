#!/bin/sh
docker build -t open-aps-db:latest ./open-aps-db && \
  docker build -t open-aps-registration-site:latest ./registration-site

