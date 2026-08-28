FROM uhub.service.ucloud.cn/techwu/python:3.14.4-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/georank-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/georank-entrypoint

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/georank-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
