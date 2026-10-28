#!/usr/bin/env python3
"""Analyze the channel-capacity benchmark and auto-tuner results.

Reads results/bench.csv, results/bench_ptr.csv, results/tuner.csv.
Writes paper/figs/*.pdf, paper/gen/tables.tex, results/summary.json and
prints a plain-text digest of every number the paper cites.
"""
import csv
import json
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BENCH = "results/bench.csv"
BENCH_PTR = "results/bench_ptr.csv"
TUNER = "results/tuner.csv"

CAPS = [0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512]
PATTERNS = ["1x1", "1x4", "4x1", "4x4"]


def read_rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def mean_std(xs):
    m = statistics.mean(xs)
    s = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return m, s


def main():
    bench = read_rows(BENCH)
    bench_ptr = read_rows(BENCH_PTR) if _exists(BENCH_PTR) else []
    tuner = read_rows(TUNER) if _exists(TUNER) else []

    # ---- characterization sweep -------------------------------------
    cell = defaultdict(lambda: {"tp": [], "p50": [], "p99": [], "mean": []})
    for r in bench:
        k = (r["pattern"], int(r["buf"]))
        cell[k]["tp"].append(float(r["tp_mps"]))
        cell[k]["p50"].append(float(r["p50_ns"]))
        cell[k]["p99"].append(float(r["p99_ns"]))
        cell[k]["mean"].append(float(r["mean_ns"]))

    summary = {"caps": CAPS, "patterns": PATTERNS, "per_pattern": {}}

    # ---- figures ------------------------------------------------------
    xlab = ["0\n(unbuf)" if c == 0 else str(c) for c in CAPS]
    pos = list(range(len(CAPS)))

    fig, axes = plt.subplots(2, 2, figsize=(9.6, 6.4), sharex=True)
    best_info = {}
    for ax, pat in zip(axes.flat, PATTERNS):
        xs, ys, es = [], [], []
        for i, c in enumerate(CAPS):
            tp, std = mean_std(cell[(pat, c)]["tp"])
            xs.append(i); ys.append(tp / 1e6); es.append(std / 1e6)
            if c != 0 and (pat not in best_info or tp > best_info[pat][1]):
                best_info[pat] = (c, tp)
        tp0, _ = mean_std(cell[(pat, 0)]["tp"])
        ax.errorbar(xs, ys, yerr=es, marker="o", ms=4, lw=1.4, capsize=2)
        b, tp = best_info[pat]
        bi = CAPS.index(b)
        ax.plot([bi], [tp / 1e6], marker="*", ms=14, color="crimson", ls="none")
        ax.axvline(0, color="gray", lw=0.7, ls=":")
        ax.set_title(f"{pat}   (unbuffered: {tp0/1e6:.2f} M/s)", fontsize=10)
        ax.set_ylabel("throughput (M msg/s)")
        ax.grid(alpha=0.3)
    for ax in axes[1]:
        ax.set_xticks(pos); ax.set_xticklabels(xlab, fontsize=8)
        ax.set_xlabel("buffer capacity")
    fig.tight_layout()
    fig.savefig("paper/figs/fig_tp.pdf")
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(9.6, 6.4), sharex=True)
    for ax, pat in zip(axes.flat, PATTERNS):
        ys, es = [], []
        for i, c in enumerate(CAPS):
            p99, std = mean_std(cell[(pat, c)]["p99"])
            ys.append(p99 / 1000.0); es.append(std / 1000.0)
        ax.errorbar(pos, ys, yerr=es, marker="o", ms=4, lw=1.4, capsize=2, color="darkgreen")
        b, _ = best_info[pat]
        bi = CAPS.index(b)
        ax.plot([bi], [ys[bi]], marker="*", ms=14, color="crimson", ls="none")
        ax.set_yscale("log")
        ax.set_title(pat, fontsize=10)
        ax.set_ylabel("p99 latency (µs)")
        ax.grid(alpha=0.3, which="both")
    for ax in axes[1]:
        ax.set_xticks(pos); ax.set_xticklabels(xlab, fontsize=8)
        ax.set_xlabel("buffer capacity")
    fig.tight_layout()
    fig.savefig("paper/figs/fig_p99.pdf")
    plt.close(fig)

    # ---- value vs pointer (4x4) ---------------------------------------
    ptr_cell = defaultdict(lambda: {"tp": [], "p99": []})
    for r in bench_ptr:
        k = int(r["buf"])
        ptr_cell[k]["tp"].append(float(r["tp_mps"]))
        ptr_cell[k]["p99"].append(float(r["p99_ns"]))

    if ptr_cell:
        fig, ax = plt.subplots(figsize=(6.4, 3.8))
        yv, ev, yp, ep = [], [], [], []
        for i, c in enumerate(CAPS):
            m, s = mean_std(cell[("4x4", c)]["tp"]); yv.append(m / 1e6); ev.append(s / 1e6)
            m, s = mean_std(ptr_cell[c]["tp"]); yp.append(m / 1e6); ep.append(s / 1e6)
        ax.errorbar(pos, yv, yerr=ev, marker="o", ms=4, lw=1.4, capsize=2, label="value channel (chan envelope)")
        ax.errorbar(pos, yp, yerr=ep, marker="s", ms=4, lw=1.4, capsize=2, label="pointer channel (chan *envelope)")
        ax.set_xticks(pos); ax.set_xticklabels(xlab, fontsize=8)
        ax.set_xlabel("buffer capacity")
        ax.set_ylabel("throughput (M msg/s)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        ax.set_title("4x4 topology", fontsize=10)
        fig.tight_layout()
        fig.savefig("paper/figs/fig_valptr.pdf")
        plt.close(fig)

        summary["val_vs_ptr"] = {}
        for c in CAPS:
            mv, sv = mean_std(cell[("4x4", c)]["tp"])
            mp, sp = mean_std(ptr_cell[c]["tp"])
            summary["val_vs_ptr"][c] = {"val_mps": mv, "ptr_mps": mp}


    # ---- tuner ---------------------------------------------------------
    t_rows = [r for r in tuner if r["kind"] == "tuner"]
    p_rows = [r for r in tuner if r["kind"] == "probe"]

    # probe-vector robustness: mean probe score per cap (across trials)
    probe_mean = defaultdict(list)
    for r in p_rows:
        probe_mean[(r["pattern"], int(r["cap"]))].append(float(r["score_mps"]))

    # probe vs sweep rank correlation per topology + fig_probe.pdf
    fig, axes = plt.subplots(2, 2, figsize=(9.6, 6.4), sharex=True)
    probe_rho = {}
    for ax, pat in zip(axes.flat, PATTERNS):
        sweep, probes = [], []
        for i, c in enumerate(CAPS):
            m, s = mean_std(cell[(pat, c)]["tp"])
            sweep.append(m / 1e6)
            probes.append(statistics.mean(probe_mean[(pat, c)]) / 1e6)
        probe_rho[pat] = spearman(sweep, probes)
        ax.plot(pos, sweep, marker="o", ms=4, lw=1.4, label="sweep (2000 ms windows)")
        ax.plot(pos, probes, marker="s", ms=4, lw=1.4, ls="--", label="tuner probes (500 ms)")
        ax.set_title(f"{pat}  (rank corr. {probe_rho[pat]:.2f})", fontsize=10)
        ax.set_ylabel("throughput (M msg/s)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    for ax in axes[1]:
        ax.set_xticks(pos); ax.set_xticklabels(xlab, fontsize=8)
        ax.set_xlabel("buffer capacity")
    fig.tight_layout()
    fig.savefig("paper/figs/fig_probe.pdf")
    plt.close(fig)

    summary["tuner"] = {}
    for pat in PATTERNS:
        chosen = [int(r["chosen"]) for r in t_rows if r["pattern"] == pat]
        elapsed = [float(r["elapsed_ms"]) for r in t_rows if r["pattern"] == pat]
        counts = defaultdict(int)
        for c in chosen:
            counts[c] += 1
        modal = max(sorted(counts), key=lambda c: counts[c])
        modal_n = counts[modal]
        sc = [float(r["score_mps"]) for r in t_rows
              if r["pattern"] == pat and int(r["chosen"]) == modal]

        # probe-frame oracle: best mean probe score over all candidate caps,
        # measured under the tuner's own conditions
        pf_b, pf_tp = 0, -1.0
        for c in CAPS:
            m = statistics.mean(probe_mean[(pat, c)])
            if m > pf_tp:
                pf_tp, pf_b = m, c

        # sweep-frame oracle from the characterization runs
        best_b, best_tp = 0, -1.0
        for c in CAPS:
            m, _ = mean_std(cell[(pat, c)]["tp"])
            if m > best_tp:
                best_tp, best_b = m, c

        summary["tuner"][pat] = {
            "chosen": chosen,
            "modal_chosen": modal,
            "modal_n": modal_n,
            "mean_elapsed_s": statistics.mean(elapsed) if elapsed else 0.0,
            "score_at_modal": statistics.mean(sc) if sc else 0.0,
            "probe_oracle_b": pf_b,
            "probe_oracle_tp": pf_tp,
            "oracle_b": best_b,
            "oracle_tp": best_tp,
            "probe_rho": probe_rho[pat],
        }

    # ---- digest + tables ----------------------------------------------
    digest = []
    add = digest.append

    for pat in PATTERNS:
        tp0, s0 = mean_std(cell[(pat, 0)]["tp"])
        b, btp = best_info[pat]
        tpb, sb = mean_std(cell[(pat, b)]["tp"])
        p99_0, _ = mean_std(cell[(pat, 0)]["p99"])
        p99_b, _ = mean_std(cell[(pat, b)]["p99"])
        p99_512, _ = mean_std(cell[(pat, 512)]["p99"])
        p50_0, _ = mean_std(cell[(pat, 0)]["p50"])
        p50_b, _ = mean_std(cell[(pat, b)]["p50"])
        p50_512, _ = mean_std(cell[(pat, 512)]["p50"])
        # find smallest cap reaching >=99% of best tp
        reach = None
        for c in CAPS:
            m, _ = mean_std(cell[(pat, c)]["tp"])
            if m >= 0.99 * btp:
                reach = c
                break
        add(f"[{pat}] unbuf tp {tp0:.2f}±{s0:.2f} M/s; best cap {b} -> {btp:.2f}±{sb:.2f} M/s "
            f"({(btp/tp0-1)*100:.1f}% over unbuffered); 99%-of-best at cap {reach}; "
            f"p50: unbuf {p50_0:.0f}ns best {p50_b:.0f}ns cap512 {p50_512:.0f}ns; "
            f"p99: unbuf {p99_0:.0f}ns best {p99_b:.0f}ns cap512 {p99_512:.0f}ns")

    for pat in PATTERNS:
        t = summary["tuner"][pat]
        parity = t["score_at_modal"] / t["probe_oracle_tp"] * 100
        add(f"[tuner {pat}] modal chosen {t['modal_chosen']} ({t['modal_n']}/10; all: {t['chosen']}); "
            f"sweep-oracle B={t['oracle_b']} tp {t['oracle_tp']:.2f} M/s; "
            f"probe-frame best B={t['probe_oracle_b']} tp {t['probe_oracle_tp']:.2f} M/s; "
            f"tuner tp {t['score_at_modal']:.2f} M/s = {parity:.2f}% of probe-frame best; "
            f"rank corr {t['probe_rho']:.2f}; decision time {t['mean_elapsed_s']:.2f}s")

    if ptr_cell:
        mv, _ = mean_std(cell[("4x4", 512)]["tp"])
        mp, _ = mean_std(ptr_cell[512]["tp"])
        mb, _ = mean_std(cell[("4x4", 16)]["tp"])
        mpb, _ = mean_std(ptr_cell[16]["tp"])
        add(f"[val-vs-ptr 4x4] cap16: val {mb:.2f} vs ptr {mpb:.2f} M/s; cap512: val {mv:.2f} vs ptr {mp:.2f} M/s")

    print("\n".join(digest))

    with open("results/summary.json", "w") as f:
        json.dump(summary, f, indent=1)

    # ---- LaTeX tables --------------------------------------------------
    lines = []
    lines.append("% generated by analysis/plots.py — do not edit\n")
    lines.append("\\begin{table}[t]\\centering\\small\n")
    lines.append("\\caption{Mean throughput (M messages/s, $\\pm$ one standard deviation, $n=5$) "
                 "by buffer capacity and topology.}\\label{tab:sweep}\n")
    lines.append("\\begin{tabular}{lrrrr}\\toprule\n")
    lines.append("Capacity & 1x1 & 1x4 & 4x1 & 4x4\\\\\\midrule\n")
    for c in CAPS:
        cols = []
        for pat in PATTERNS:
            m, s = mean_std(cell[(pat, c)]["tp"])
            cols.append(f"{m:.2f}~$\\pm$~{s:.2f}")
        name = "0 (unbuf.)" if c == 0 else str(c)
        lines.append(f"{name} & " + " & ".join(cols) + "\\\\\n")
    lines.append("\\bottomrule\\end{tabular}\\end{table}\n\n")

    lines.append("\\begin{table}[t]\\centering\\small\n")
    lines.append("\\caption{Auto-tuner vs.\\ exhaustive sweep. Tuner capacity is the modal "
                 "selection over 10 independent invocations; tuner throughput is its probe "
                 "score at that capacity; the probe-frame best is the best capacity under "
                 "the tuner's own 500\\,ms probes.}\\label{tab:tuner}\n")
    lines.append("\\begin{tabular}{lrrrrrr}\\toprule\n")
    lines.append("Topology & Sweep best & Sweep tp & Tuner & Tuner tp & Probe-frame best & \\\\\n")
    lines.append(" & capacity & (M/s) & capacity & (M/s) & capacity & Parity \\\\\n")
    lines.append("\\midrule\n")
    for pat in PATTERNS:
        t = summary["tuner"][pat]
        parity = t["score_at_modal"] / t["probe_oracle_tp"] * 100
        lines.append(f"{pat} & {t['oracle_b']} & {t['oracle_tp']:.2f} & "
                     f"{t['modal_chosen']} ({t['modal_n']}/10) & {t['score_at_modal']:.2f} & "
                     f"{t['probe_oracle_b']} & {parity:.1f}\\%\\\\\n")
    lines.append("\\bottomrule\\end{tabular}\\end{table}\n")

    with open("paper/gen/tables.tex", "w") as f:
        f.writelines(lines)


def _exists(p):
    import os
    return os.path.exists(p)


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for pos_, i in enumerate(order):
        r[i] = float(pos_)
    return r


def spearman(a, b):
    ra, rb = rank(a), rank(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


if __name__ == "__main__":
    main()
