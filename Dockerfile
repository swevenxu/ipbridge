FROM python:3.12-alpine

WORKDIR /app
COPY app.py ./
COPY static ./static
RUN mkdir -p /app/uploads

ENV PORT=8080 \
    STORE=/app/uploads \
    MAX_MB=100

EXPOSE 8080
VOLUME ["/app/uploads"]

# PIN and SECRET belong in the environment, not the image:
#   docker run -e PIN=2468 -v photos:/app/uploads -p 8080:8080 photobridge
CMD ["python3", "app.py"]
