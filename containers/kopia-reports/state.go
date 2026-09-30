package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type sourceState struct {
	LastReport  int64            `json:"lastReport"`
	LastSuccess int64            `json:"lastSuccess"`
	Status      string           `json:"status"`
	Duration    float64          `json:"duration"`
	Counts      map[string]int64 `json:"counts"`
}

// Store keeps the newest report per source and writes it to a file, so a
// restart does not forget when each source last completed.
type Store struct {
	mu       sync.Mutex
	file     string
	Sources  map[string]*sourceState `json:"sources"`
	Unparsed int64                   `json:"unparsed"`
}

// LoadStore reads the state file; a missing file starts an empty store.
func LoadStore(file string) (*Store, error) {
	s := &Store{file: file, Sources: map[string]*sourceState{}}
	data, err := os.ReadFile(file)
	if errors.Is(err, os.ErrNotExist) {
		return s, nil
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, s); err != nil {
		return nil, fmt.Errorf("reading %s: %w", file, err)
	}
	if s.Sources == nil {
		s.Sources = map[string]*sourceState{}
	}
	return s, nil
}

// Apply records the reports received at now.
func (s *Store) Apply(reports []Report, now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if len(reports) == 0 {
		s.Unparsed++
	}
	for _, r := range reports {
		st := s.Sources[r.Source]
		if st == nil {
			st = &sourceState{Counts: map[string]int64{}}
			s.Sources[r.Source] = st
		}
		st.LastReport = now.Unix()
		st.Status = r.Status
		st.Duration = r.Duration.Seconds()
		st.Counts[r.Status]++
		// A snapshot that finished with warnings still completed.
		if r.Status == "success" || r.Status == "warnings" {
			st.LastSuccess = now.Unix()
		}
	}
	return s.save()
}

func (s *Store) save() error {
	data, err := json.Marshal(s)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(s.file), ".state-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), s.file)
}

// Render writes the state in the Prometheus text format.
func (s *Store) Render(w io.Writer) {
	s.mu.Lock()
	defer s.mu.Unlock()

	names := make([]string, 0, len(s.Sources))
	for n := range s.Sources {
		names = append(names, n)
	}
	sort.Strings(names)

	family := func(name, typ, help string) {
		fmt.Fprintf(w, "# HELP %s %s\n# TYPE %s %s\n", name, help, name, typ)
	}

	family("kopia_report_last_timestamp_seconds", "gauge", "When the newest report for the source arrived.")
	for _, n := range names {
		fmt.Fprintf(w, "kopia_report_last_timestamp_seconds{source=%s} %d\n", quote(n), s.Sources[n].LastReport)
	}

	family("kopia_report_last_success_timestamp_seconds", "gauge", "When the newest completed snapshot (success or warnings) was reported for the source.")
	for _, n := range names {
		if ts := s.Sources[n].LastSuccess; ts > 0 {
			fmt.Fprintf(w, "kopia_report_last_success_timestamp_seconds{source=%s} %d\n", quote(n), ts)
		}
	}

	family("kopia_report_status", "gauge", "1 for the status of the newest report for the source.")
	for _, n := range names {
		for _, status := range statuses {
			v := 0
			if s.Sources[n].Status == status {
				v = 1
			}
			fmt.Fprintf(w, "kopia_report_status{source=%s,status=%s} %d\n", quote(n), quote(status), v)
		}
	}

	family("kopia_report_last_duration_seconds", "gauge", "How long the newest snapshot of the source took.")
	for _, n := range names {
		fmt.Fprintf(w, "kopia_report_last_duration_seconds{source=%s} %g\n", quote(n), s.Sources[n].Duration)
	}

	family("kopia_reports_total", "counter", "Reports received, by source and status.")
	for _, n := range names {
		for _, status := range statuses {
			fmt.Fprintf(w, "kopia_reports_total{source=%s,status=%s} %d\n", quote(n), quote(status), s.Sources[n].Counts[status])
		}
	}

	family("kopia_reports_unparsed_total", "counter", "Notifications received that held no snapshot report.")
	fmt.Fprintf(w, "kopia_reports_unparsed_total %d\n", s.Unparsed)
}

func quote(v string) string {
	r := strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", `\n`)
	return `"` + r.Replace(v) + `"`
}
