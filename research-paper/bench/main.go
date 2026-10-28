// Command bench measures the throughput and latency of a shared Go channel
// under different buffer capacities and sender/receiver ratios.
package main

import (
	"flag"
	"fmt"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// envelope is the message routed through the channel. Its size (112 bytes)
// is realistic for a small work item; senders stamp the dispatch time so
// receivers can reconstruct queueing latency.
type envelope struct {
	seq     uint64
	sendNs  int64
	payload [96]byte
}

func main() {
	pattern := flag.String("pattern", "1x1", "sender x receiver ratio, e.g. 4x4")
	buf := flag.Int("buf", 0, "channel buffer capacity (0 = unbuffered)")
	warmMs := flag.Int("warm-ms", 400, "warmup ms (discarded)")
	runMs := flag.Int("run-ms", 1600, "measured ms")
	procs := flag.Int("procs", runtime.NumCPU(), "GOMAXPROCS")
	ptr := flag.Bool("ptr", false, "route *envelope instead of envelope")
	csvPath := flag.String("csv", "", "append result row to this CSV file")
	tag := flag.String("tag", "", "free-form run tag")
	flag.Parse()

	runtime.GOMAXPROCS(*procs)

	s, r := parsePattern(*pattern)

	origin := time.Now()
	rel := func() int64 { return time.Since(origin).Nanoseconds() }
	mStart := int64(*warmMs) * 1e6
	mEnd := mStart + int64(*runMs)*1e6

	var measured atomic.Int64
	var all []int64
	var mu sync.Mutex

	if *ptr {
		ch := make(chan *envelope, *buf)
		var recvWg, sendWg sync.WaitGroup
		for i := 0; i < r; i++ {
			recvWg.Add(1)
			go func() {
				defer recvWg.Done()
				for e := range ch {
					if e.sendNs >= mStart && e.sendNs < mEnd {
						mu.Lock()
						all = append(all, rel()-e.sendNs)
						mu.Unlock()
						measured.Add(1)
					}
				}
			}()
		}
		for i := 0; i < s; i++ {
			sendWg.Add(1)
			go func() {
				defer sendWg.Done()
				var seq uint64
				for rel() < mEnd {
					seq++
					ch <- &envelope{seq: seq, sendNs: rel()}
				}
			}()
		}
		sendWg.Wait()
		close(ch)
		recvWg.Wait()
	} else {
		ch := make(chan envelope, *buf)
		var recvWg, sendWg sync.WaitGroup
		resCh := make(chan []int64, r)
		for i := 0; i < r; i++ {
			recvWg.Add(1)
			go func() {
				defer recvWg.Done()
				var xs []int64
				for e := range ch {
					if e.sendNs >= mStart && e.sendNs < mEnd {
						xs = append(xs, rel()-e.sendNs)
						measured.Add(1)
					}
				}
				resCh <- xs
			}()
		}
		for i := 0; i < s; i++ {
			sendWg.Add(1)
			go func() {
				defer sendWg.Done()
				var e envelope
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
		recvWg.Wait()
		close(resCh)
		for xs := range resCh {
			all = append(all, xs...)
		}
	}

	sort.Slice(all, func(i, j int) bool { return all[i] < all[j] })

	secs := float64(*runMs) / 1000.0
	tp := float64(measured.Load()) / secs
	p50 := pct(all, 0.50)
	p99 := pct(all, 0.99)
	mean := 0.0
	if len(all) > 0 {
		var sum float64
		for _, x := range all {
			sum += float64(x)
		}
		mean = sum / float64(len(all))
	}

	row := fmt.Sprintf("bench,%s,%d,%d,%s,%d,%.2f,%d,%d,%.1f",
		*pattern, *buf, *procs, *tag, measured.Load(), tp, p50, p99, mean)
	fmt.Println(row)

	if *csvPath != "" {
		f, err := os.OpenFile(*csvPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if fi, _ := f.Stat(); fi != nil && fi.Size() == 0 {
			fmt.Fprintln(f, "kind,pattern,buf,procs,tag,msgs,tp_mps,p50_ns,p99_ns,mean_ns")
		}
		fmt.Fprintln(f, row)
		f.Close()
	}
}

func parsePattern(p string) (int, int) {
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

func pct(sorted []int64, q float64) int64 {
	if len(sorted) == 0 {
		return 0
	}
	i := int(q * float64(len(sorted)-1))
	return sorted[i]
}
