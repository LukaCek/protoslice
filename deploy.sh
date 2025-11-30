#!/bin/bash
APP_NAME="protoslice"
PORT="5252"

docker build -t $APP_NAME:latest . && \
docker stop $APP_NAME 2>/dev/null || true && \
docker rm $APP_NAME 2>/dev/null || true && \
docker run -d --name $APP_NAME -p $PORT:$PORT $APP_NAME:latest
