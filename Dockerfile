FROM python:3.11-alpine

RUN apk add --no-cache git

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 8000
CMD ["python", "-u", "/app/app.py"]
