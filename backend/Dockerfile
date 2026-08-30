FROM uhub.service.ucloud.cn/techwu/python:3.14.4-slim

WORKDIR /app

# Python 依赖
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .
COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/georank-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/georank-entrypoint

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/georank-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
