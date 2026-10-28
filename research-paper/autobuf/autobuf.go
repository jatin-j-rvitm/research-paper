// Package autobuf provides a measurement-driven auto-tuner for choosing the
// buffer capacity of Go channels.
//
// The capacity of a channel changes how senders and receivers overlap, but
// there is little principled guidance for picking one. autobuf treats the
// capacity as a hyperparameter: it probes the real pipeline (built by the
// caller) once per candidate capacity, scores each probe by throughput, and
// reports the winner.
package autobuf

import (
	"sort"
	"time"
)

// Config controls an auto-tuning run.
type Config struct {
	// Candidate buffer capacities, in any order. A capacity of 0 selects
	// an unbuffered channel.
	Candidates []int

	// Warmup is the warm-up time given to each probe run before its score
	// is recorded. run itself must discard the warm-up window.
	Warmup time.Duration

	// Measure is how long each probe run is measured.
	Measure time.Duration

	// Trials is the number of independent probe runs per candidate. The
	// median score across trials is used, which makes the tuner robust
	// against scheduler noise. Zero means one trial.
	Trials int
}

// AutoSize evaluates run(b) for every candidate capacity b and returns the
// capacity with the best (highest) median score, together with the median
// score of every candidate, in the order the candidates were given.
//
// The run function must build its own short-lived pipeline around a buffered
// channel of capacity b, keep it running for the full warm-up plus measure
// window, and return a throughput-style score (higher is better). Warm-up
// traffic must be excluded by run itself, typically by timestamping messages
// and discarding those sent before the measure window opens.
//
// AutoSize is blocking and takes O(len(Candidates) * Trials) probe runs. Call
// it once at pipeline start-up, and optionally re-run it when the workload
// shape changes.
func AutoSize(cfg Config, run func(b int) float64) (best int, scores []float64) {
	trials := cfg.Trials
	if trials < 1 {
		trials = 1
	}

	best = -1
	bestScore := -1e308

	for _, b := range cfg.Candidates {
		xs := make([]float64, 0, trials)
		for t := 0; t < trials; t++ {
			xs = append(xs, run(b))
		}
		sort.Float64s(xs)
		m := median(xs)
		scores = append(scores, m)
		if m > bestScore {
			bestScore = m
			best = b
		}
	}

	return best, scores
}

func median(xs []float64) float64 {
	n := len(xs)
	if n == 0 {
		return 0
	}
	if n%2 == 1 {
		return xs[n/2]
	}
	return (xs[n/2-1] + xs[n/2]) / 2
}
