#!/bin/bash

# Start your app (adjust this line as needed)
#uvicorn main:app --host 0.0.0.0 --port 8000

gunicorn main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 600
