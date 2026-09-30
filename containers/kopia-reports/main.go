// kopia-reports receives the snapshot reports that a Kopia server sends to a
// webhook and exposes, per backed-up folder, when it last completed and how
// its newest snapshot ended.
package main

import (
	"context"
	"crypto/subtle"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

const maxBody = 1 << 20

func main() {
	listen := env("LISTEN", ":8080")
	stateFile := env("STATE_FILE", "/data/state.json")
	token := os.Getenv("WEBHOOK_TOKEN")

	store, err := LoadStore(stateFile)
	if err != nil {
		log.Fatalf("loading state: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		io.WriteString(w, "OK\n")
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		store.Render(w)
	})
	webhook := webhookHandler(store, token)
	mux.HandleFunc("POST /", webhook)
	mux.HandleFunc("PUT /", webhook)

	srv := &http.Server{
		Addr:              listen,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		srv.Shutdown(shutdown)
	}()

	log.Printf("listening on %s, state in %s", listen, stateFile)
	if err := srv.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}

func webhookHandler(store *Store, token string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if token != "" {
			got := r.Header.Get("Authorization")
			if subtle.ConstantTimeCompare([]byte(got), []byte("Bearer "+token)) != 1 {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
		}

		body, err := io.ReadAll(io.LimitReader(r.Body, maxBody))
		if err != nil {
			http.Error(w, "unreadable body", http.StatusBadRequest)
			return
		}

		reports := ParseReports(string(body))
		log.Printf("notification: %d bytes, %d snapshot reports: %q", len(body), len(reports), truncate(string(body), 1500))
		for _, rep := range reports {
			log.Printf("report: source=%s status=%s duration=%s error=%q", rep.Source, rep.Status, rep.Duration, rep.Error)
		}

		if err := store.Apply(reports, time.Now()); err != nil {
			log.Printf("saving state: %v", err)
			http.Error(w, "could not save state", http.StatusInternalServerError)
			return
		}
		io.WriteString(w, "ok\n")
	}
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
