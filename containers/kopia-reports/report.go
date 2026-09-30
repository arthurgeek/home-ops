package main

import (
	"path"
	"regexp"
	"strings"
	"time"
)

// Report is the result of one snapshot, as listed in a Kopia
// snapshot-report notification.
type Report struct {
	Path     string
	Source   string // last element of Path, used as the metric label
	Status   string // success, warnings, incomplete or fatal
	Duration time.Duration
	Error    string
}

// The statuses Kopia reports for a snapshot.
var statuses = []string{"success", "warnings", "incomplete", "fatal"}

var (
	pathRe   = regexp.MustCompile(`(?m)^[ \t]*Path:[ \t]*(.+?)[ \t]*$`)
	statusRe = regexp.MustCompile(`(?m)^[ \t]*Status:[ \t]*(\S+)`)
	durRe    = regexp.MustCompile(`(?m)^[ \t]*Duration:[ \t]*(\S+)`)
	errRe    = regexp.MustCompile(`(?m)^[ \t]*Error:[ \t]*(.+?)[ \t]*$`)
)

// ParseReports reads the plain-text snapshot-report that Kopia sends, one
// block per snapshot starting at its "Path:" line. Text without any such
// block, such as the test notification, yields no reports.
func ParseReports(body string) []Report {
	body = strings.ReplaceAll(body, "\r\n", "\n")
	locs := pathRe.FindAllStringSubmatchIndex(body, -1)

	var reports []Report
	for i, loc := range locs {
		end := len(body)
		if i+1 < len(locs) {
			end = locs[i+1][0]
		}
		block := body[loc[0]:end]

		p := body[loc[2]:loc[3]]
		r := Report{Path: p, Source: path.Base(p)}

		m := statusRe.FindStringSubmatch(block)
		if m == nil {
			continue
		}
		r.Status = normalizeStatus(m[1])

		if m := durRe.FindStringSubmatch(block); m != nil {
			if d, err := time.ParseDuration(m[1]); err == nil {
				r.Duration = d
			}
		}
		if m := errRe.FindStringSubmatch(block); m != nil {
			r.Error = m[1]
		}
		reports = append(reports, r)
	}
	return reports
}

func normalizeStatus(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	for _, known := range statuses {
		if s == known {
			return s
		}
	}
	return "fatal"
}
