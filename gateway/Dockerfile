FROM golang:1.25.0-alpine AS build
ARG GOPROXY=https://goproxy.cn,direct
ARG GOSUMDB=sum.golang.google.cn
ENV GOPROXY=${GOPROXY} \
    GOSUMDB=${GOSUMDB}
WORKDIR /src
COPY gateway/go.mod gateway/go.sum* ./
RUN go mod download
COPY gateway/ .
RUN CGO_ENABLED=0 go test -tags=eino ./internal/runtime && \
    go build -tags=eino -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway

# CGO is disabled above, so the gateway can run from scratch. Keep the CA
# bundle for outbound HTTPS calls (for example, embedding/model providers)
# without requiring a separate runtime image registry.
FROM scratch
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build --chown=65532:65532 /out/gateway /gateway
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/gateway"]
