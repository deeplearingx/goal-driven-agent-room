FROM golang:1.25.0-alpine AS build
WORKDIR /src
COPY gateway/go.mod gateway/go.sum* ./
RUN go mod download
COPY gateway/ .
RUN CGO_ENABLED=0 go test -tags=eino ./internal/runtime && \
    go build -tags=eino -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gateway /gateway
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/gateway"]
