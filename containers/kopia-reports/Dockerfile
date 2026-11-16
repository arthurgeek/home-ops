FROM public.ecr.aws/docker/library/golang:1.27-alpine@sha256:70bd7ddea8b6cbe5d6943cde279d6b67feb10cc924fb9100d1d8757c1575b280 AS build
WORKDIR /src
ENV CGO_ENABLED=0
COPY go.mod ./
COPY *.go ./
RUN go vet ./... && go test ./...
RUN go build -trimpath -ldflags="-s -w" -o /out/kopia-reports .

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /out/kopia-reports /kopia-reports
ENTRYPOINT ["/kopia-reports"]
