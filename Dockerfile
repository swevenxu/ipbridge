FROM python:3.12-alpine

WORKDIR /app
COPY app.py ./
COPY static ./static
RUN mkdir -p /app/uploads

ENV PORT=8080 \
    STORE=/app/uploads

EXPOSE 8080
VOLUME ["/app/uploads"]

# Photos live in the volume, never in the image:
#   docker run -v photos:/app/uploads -p 8080:8080 photobridge
CMD ["python3", "app.py"]
