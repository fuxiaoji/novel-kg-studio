"""Apply two idempotent, exact-source corrections to the generated manuscript."""
from pathlib import Path
path=Path(__file__).resolve().parents[1]/"paper"/"manuscript.tex"
text=path.read_text(encoding="utf-8")
old="The five graph conditions (G1--G5) are the previously locked graph evidence routes. They differ only in graph-context ordering or the fixed graph-only aggregation rule; they never consult a baseline prediction, gold answer, or gold paragraph. G1--G3 expose deterministic permutations of the same graph evidence, G4 applies the pre-specified agreement/fallback rule, and G5 applies the fixed graph-vote aggregation. Because some routes were developed while inspecting part of these data, all five are labelled exploratory rather than a pristine held-out test."
new="The five graph conditions (G1--G5) never consult a baseline prediction, gold answer, or gold paragraph. G1--G3 expose deterministic option-order permutations over the same graph evidence, G4 is their deterministic graph-only majority, and G5 is a tighter source-expansion traversal whose archived records explicitly set \\texttt{baseline\\_access=false}. These are five experimental conditions rather than five independent graph-construction algorithms. Because G4/G5 were selected or developed while inspecting this corpus, all five are labelled exploratory rather than a pristine held-out test. Earlier tail-fallback composites are retained in the historical archive but excluded from the main graph-method table."
if old in text:text=text.replace(old,new,1)
marker="\\section{Discussion}"
insert=r'''\begin{figure}[ht]
\centering
\includegraphics[width=0.92\linewidth]{generated/dqa30_pairwise.pdf}
\caption{Paired accuracy differences for the best pooled graph condition against each baseline, with novel-clustered bootstrap intervals and question-level wins/losses. Holm-adjusted values cover the 15 planned graph--baseline comparisons.}
\label{fig:paired}
\end{figure}

\begin{figure}[ht]
\centering
\includegraphics[width=0.98\linewidth]{generated/dqa30_relationships.pdf}
\caption{Exploratory novel-level relationships among gold-paragraph mapping recall, graph isolation, novel length, and G5 performance. Cohorts are shown separately; these associations are not causal tests.}
\label{fig:relationships}
\end{figure}

'''
if "\\label{fig:paired}" not in text:text=text.replace(marker,insert+marker,1)
path.write_text(text,encoding="utf-8")
print(path)
