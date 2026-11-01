FROM golang:1.27-bookworm
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
 && uv python install 3.12 && chmod -R a+rX /opt/uv-python \
 && GOBIN=/usr/local/bin go install github.com/swaggo/swag/v2/cmd/swag@v2.0.0-rc6 \
 && rm -rf /root/.cache /go/pkg \
 && mkdir -p /data && chmod 1777 /data
COPY probe.sh /usr/local/bin/probe.sh
ENTRYPOINT ["bash", "/usr/local/bin/probe.sh"]
