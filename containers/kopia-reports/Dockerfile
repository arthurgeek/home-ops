FROM public.ecr.aws/docker/library/golang:1.27-alpine@sha256:b8c9b82affbbb82a020bbcf998dad2ba9516944ac7e883524642a481a9505e9c AS build
WORKDIR /src
ENV CGO_ENABLED=0
COPY go.mod ./
COPY *.go ./
RUN go vet ./... && go test ./...
RUN go build -trimpath -ldflags="-s -w" -o /out/kopia-reports .

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /out/kopia-reports /kopia-reports
ENTRYPOINT ["/kopia-reports"]
