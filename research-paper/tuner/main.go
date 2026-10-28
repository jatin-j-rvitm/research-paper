// Command tuner measures how well the autobuf auto-tuner tracks the best
// channel capacity found by an exhaustive sweep.
package main

import (
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"autobuf/autobuf"
)

// env mirrors bench's envelope so probe throughput is directly comparable
// with the characterization runs.
type env struct {
	seq     uint64
	sendNs  int64
	payload [96]byte
}

func main() {
	patterns := flag.String("patterns", "1x1,1x4,4x1,4x4", "comma-separated SxR patterns")
	evalTrials := flag.Int("eval-trials", 10, "independent auto-tuner invocations per pattern")
	bufTrials := flag.Int("buf-trials", 1, "probe runs per candidate inside the tuner")
	warmMs := flag.Int("warm-ms", 150, "per-probe warmup ms")
	runMs := flag.Int("run-ms", 350, "per-probe measured ms")
	csvPath := flag.String("csv", "results/tuner.csv", "output CSV")
	flag.Parse()

	cands := []int{0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512}

	f, err := os.OpenFile(*csvPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer f.Close()
	fmt.Fprintln(f, "kind,pattern,trial,cap,chosen,elapsed_ms,score_mps")

	for _, p := range strings.Split(*patterns, ",") {
		s, r := parseSR(p)
		run := pipeline(s, r, *warmMs, *runMs)
		for t := 0; t < *evalTrials; t++ {
			t0 := time.Now()
			chosen, scores := autobuf.AutoSize(autobuf.Config{
				Candidates: cands,
				Warmup:     time.Duration(*warmMs) * time.Millisecond,
				Measure:    time.Duration(*runMs) * time.Millisecond,
				Trials:     *bufTrials,
			}, run)
			elapsed := time.Since(t0).Seconds()

			for i, c := range cands {
				fmt.Fprintf(f, "probe,%s,%d,%d,,,%.2f\n", p, t, c, scores[i])
			}
			row := fmt.Sprintf("tuner,%s,%d,,%d,%0.3f,%.2f", p, t, chosen, elapsed, scoreOf(cands, scores, chosen))
			fmt.Println(row)
			fmt.Fprintln(f, row)
		}
	}
}

// pipeline builds a fresh sender/receiver pipeline around a buffered channel
// of capacity b, runs it for warmup + run milliseconds, and returns the
// measured throughput in messages per second.
func pipeline(s, r, warmMs, runMs int) func(b int) float64 {
	return func(b int) float64 {
		origin := time.Now()
		rel := func() int64 { return time.Since(origin).Nanoseconds() }
		mStart := int64(warmMs) * 1e6
		mEnd := mStart + int64(runMs)*1e6

		ch := make(chan env, b)
		var measured atomic.Int64

		for i := 0; i < r; i++ {
			go func() {
				for e := range ch {
					if e.sendNs >= mStart && e.sendNs < mEnd {
						measured.Add(1)
					}
				}
			}()
		}

		var sendWg sync.WaitGroup
		for i := 0; i < s; i++ {
			sendWg.Add(1)
			go func() {
				defer sendWg.Done()
				var e env
				var seq uint64
				for rel() < mEnd {
					seq++
					e.seq = seq
					e.sendNs = rel()
					ch <- e
				}
			}()
		}

		sendWg.Wait()
		close(ch)
		return float64(measured.Load()) / (float64(runMs) / 1000.0)
	}
}

func parseSR(p string) (int, int) {
	parts := strings.Split(p, "x")
	if len(parts) != 2 {
		fmt.Fprintf(os.Stderr, "bad pattern %q\n", p)
		os.Exit(1)
	}
	s, err1 := strconv.Atoi(parts[0])
	r, err2 := strconv.Atoi(parts[1])
	if err1 != nil || err2 != nil || s < 1 || r < 1 {
		fmt.Fprintf(os.Stderr, "bad pattern %q\n", p)
		os.Exit(1)
	}
	return s, r
}

func scoreOf(cands []int, scores []float64, chosen int) float64 {
	for i, c := range cands {
		if c == chosen {
			return scores[i]
		}
	}
	return 0
}
