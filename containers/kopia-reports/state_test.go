package main

import (
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func render(s *Store) string {
	var b strings.Builder
	s.Render(&b)
	return b.String()
}

func TestApplyAndRender(t *testing.T) {
	s, err := LoadStore(filepath.Join(t.TempDir(), "state.json"))
	if err != nil {
		t.Fatal(err)
	}
	t1 := time.Unix(1000, 0)
	t2 := time.Unix(2000, 0)

	if err := s.Apply([]Report{{Source: "photos", Status: "success", Duration: 3 * time.Second}}, t1); err != nil {
		t.Fatal(err)
	}
	if err := s.Apply([]Report{{Source: "photos", Status: "fatal", Duration: time.Second}}, t2); err != nil {
		t.Fatal(err)
	}

	out := render(s)
	for _, want := range []string{
		`kopia_report_last_timestamp_seconds{source="photos"} 2000`,
		// A failed run does not move the last success.
		`kopia_report_last_success_timestamp_seconds{source="photos"} 1000`,
		`kopia_report_status{source="photos",status="fatal"} 1`,
		`kopia_report_status{source="photos",status="success"} 0`,
		`kopia_reports_total{source="photos",status="success"} 1`,
		`kopia_reports_total{source="photos",status="fatal"} 1`,
		`kopia_report_last_duration_seconds{source="photos"} 1`,
	} {
		if !strings.Contains(out, want+"\n") {
			t.Errorf("output lacks %q\n%s", want, out)
		}
	}
}

func TestWarningsCountAsCompleted(t *testing.T) {
	s, _ := LoadStore(filepath.Join(t.TempDir(), "state.json"))
	s.Apply([]Report{{Source: "homes", Status: "warnings"}}, time.Unix(500, 0))
	if out := render(s); !strings.Contains(out, `kopia_report_last_success_timestamp_seconds{source="homes"} 500`) {
		t.Errorf("warnings did not count as completed\n%s", out)
	}
}

func TestNoReportsCountsAsUnparsed(t *testing.T) {
	s, _ := LoadStore(filepath.Join(t.TempDir(), "state.json"))
	s.Apply(nil, time.Unix(1, 0))
	if out := render(s); !strings.Contains(out, "kopia_reports_unparsed_total 1\n") {
		t.Errorf("unparsed not counted\n%s", out)
	}
}

func TestStatePersistsAcrossRestart(t *testing.T) {
	file := filepath.Join(t.TempDir(), "state.json")
	s, _ := LoadStore(file)
	if err := s.Apply([]Report{{Source: "k8s", Status: "success"}}, time.Unix(777, 0)); err != nil {
		t.Fatal(err)
	}

	again, err := LoadStore(file)
	if err != nil {
		t.Fatal(err)
	}
	if out := render(again); !strings.Contains(out, `kopia_report_last_success_timestamp_seconds{source="k8s"} 777`) {
		t.Errorf("state was lost\n%s", out)
	}
}

func TestLabelsAreEscaped(t *testing.T) {
	s, _ := LoadStore(filepath.Join(t.TempDir(), "state.json"))
	s.Apply([]Report{{Source: `we"ird\name`, Status: "success"}}, time.Unix(1, 0))
	if out := render(s); !strings.Contains(out, `source="we\"ird\\name"`) {
		t.Errorf("label not escaped\n%s", out)
	}
}
