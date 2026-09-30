package main

import (
	"testing"
	"time"
)

// What Kopia's plain-text snapshot-report template renders.
const twoSnapshots = `Subject: Kopia snapshot report on nas

Path: /mnt/tank/photos

  Status:      success
  Start:       2026-09-30 02:00:00 UTC
  Duration:    1m4.5s
  Size:        417.2 GB
  Files:       120,343
  Directories: 4,201

Path: /mnt/tank/documents

  Status:      fatal
  Start:       2026-09-30 02:01:10 UTC
  Duration:    820ms
  Size:        1.1 MB
  Files:       12
  Directories: 3
  Error:       error uploading: PermissionDenied

  Failed Entries:

  - /mnt/tank/documents/secret.txt: permission denied

Generated at 2026-09-30 02:02:00 UTC by Kopia v0.21.1.

https://kopia.io/
`

func TestParseReports(t *testing.T) {
	got := ParseReports(twoSnapshots)
	if len(got) != 2 {
		t.Fatalf("got %d reports, want 2: %+v", len(got), got)
	}

	want := []Report{
		{Path: "/mnt/tank/photos", Source: "photos", Status: "success", Duration: 64500 * time.Millisecond},
		{Path: "/mnt/tank/documents", Source: "documents", Status: "fatal", Duration: 820 * time.Millisecond, Error: "error uploading: PermissionDenied"},
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("report %d = %+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestParseReportsIgnoresOtherText(t *testing.T) {
	for _, body := range []string{"", "Test notification from Kopia\n", "Path: /x\n\nno status here\n"} {
		if got := ParseReports(body); len(got) != 0 {
			t.Errorf("ParseReports(%q) = %+v, want none", body, got)
		}
	}
}

func TestParseReportsWindowsLineEndings(t *testing.T) {
	got := ParseReports("Path: /a/b\r\n  Status:      warnings\r\n  Duration:    2s\r\n")
	if len(got) != 1 || got[0].Source != "b" || got[0].Status != "warnings" || got[0].Duration != 2*time.Second {
		t.Fatalf("got %+v", got)
	}
}

func TestUnknownStatusCountsAsFatal(t *testing.T) {
	got := ParseReports("Path: /a\n  Status:      exploded\n")
	if len(got) != 1 || got[0].Status != "fatal" {
		t.Fatalf("got %+v", got)
	}
}
