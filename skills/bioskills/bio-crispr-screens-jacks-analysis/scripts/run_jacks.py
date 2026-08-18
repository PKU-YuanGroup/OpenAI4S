# Reference: mageck 0.5+, matplotlib 3.8+, numpy 1.26+, pandas 2.2+, scipy 1.12+ | Verify API if version differs
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# For test data, use example files from JACKS repository:
# https://github.com/felicityallen/JACKS/tree/master/examples

# Example input format
# counts.txt: sgRNA counts per sample (tab-separated)
# guidemap.txt: sgRNA to gene mapping
# replicatemap.txt: sample to experiment and condition mapping

def prepare_input_files(counts_file, output_prefix):
    '''Prepare JACKS input files from a single counts file'''
    counts = pd.read_csv(counts_file, sep='\t', index_col=0)

    # Extract guide-gene map (assumes Gene column exists)
    if 'Gene' in counts.columns:
        guide_gene = counts[['Gene']].reset_index()
        guide_gene.columns = ['sgRNA', 'Gene']
        guide_gene.to_csv(f'{output_prefix}_guidemap.txt', sep='\t', index=False)   # JACKS requires a header row
        counts = counts.drop('Gene', axis=1)

    # Save counts without gene column
    counts.to_csv(f'{output_prefix}_counts.txt', sep='\t')

    return counts

def run_jacks_analysis(counts_file, guidemap_file, replicatemap_file, output_prefix,
                       ctrl_condition='Day0', treatment_condition='Day14'):
    '''Run JACKS analysis via command line'''
    import subprocess

    cmd = [
        'python', 'run_JACKS.py',      # run from JACKS/jacks/; there is no jacks.run_JACKS module
        counts_file,
        replicatemap_file,
        guidemap_file,
        '--outprefix', output_prefix,
        '--ctrl_sample_hdr', 'Control'
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f'JACKS error: {result.stderr}')
    return result.returncode == 0

def analyze_results(gene_results_file, guide_results_file):
    '''Analyze JACKS output'''
    genes = pd.read_csv(gene_results_file, sep='\t')
    guides = pd.read_csv(guide_results_file, sep='\t')

    # JACKS writes no FDR column. Call hits on effect / posterior std (a Bayesian
    # z-equivalent); abs > 2 is the conventional cutoff.
    sig_threshold = -2

    cell_line = [c for c in genes.columns if c != 'Gene'][0]
    stds = pd.read_csv(f'{output_prefix}_gene_std_JACKS_results.txt', sep='\t')
    genes = genes[['Gene', cell_line]].merge(stds[['Gene', cell_line]], on='Gene', suffixes=('_effect', '_std'))
    genes['z'] = genes[f'{cell_line}_effect'] / genes[f'{cell_line}_std']

    essential = genes[genes['z'] < sig_threshold].sort_values('z')

    print(f'Total genes tested: {len(genes)}')
    print(f'Essential genes (effect/std < -2): {len(essential)}')
    print('\nTop 20 essential genes:')
    print(essential[['Gene', f'{cell_line}_effect', 'z']].head(20).to_string(index=False))

    # sgRNA efficacy summary
    print(f'\nsgRNA efficacy stats:')
    print(f'  Mean: {guides["X1"].mean():.3f}')
    print(f'  Median: {guides["X1"].median():.3f}')
    # efficacy<0.3: Poor sgRNAs to consider removing from future libraries.
    low_eff = (guides['X1'] < 0.3).sum()
    print(f'  Low efficacy (<0.3): {low_eff} ({100*low_eff/len(guides):.1f}%)')

    return genes, guides

def plot_results(genes, guides, output_prefix):
    '''Generate JACKS result visualizations'''
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Volcano plot
    ax = axes[0]
    effect_col = [c for c in genes.columns if c.endswith('_effect')][0]
    colors = ['red' if z < -2 else 'gray' for z in genes['z']]
    ax.scatter(genes[effect_col], -genes['z'], c=colors, alpha=0.5, s=10)
    ax.axhline(2, linestyle='--', color='black', alpha=0.5, label='effect/std = -2')
    ax.axvline(0, linestyle='-', color='gray', alpha=0.3)
    ax.set_xlabel('JACKS gene effect (negative = essential)')
    ax.set_ylabel('-(effect / posterior std)')
    ax.set_title('Gene Essentiality')
    ax.legend()

    # sgRNA efficacy histogram
    ax = axes[1]
    ax.hist(guides['X1'], bins=50, edgecolor='black', alpha=0.7)
    ax.axvline(0.5, color='red', linestyle='--', label='Efficacy = 0.5')
    ax.axvline(0.3, color='orange', linestyle='--', label='Low efficacy threshold')
    ax.set_xlabel('sgRNA Efficacy')
    ax.set_ylabel('Count')
    ax.set_title('sgRNA Efficacy Distribution')
    ax.legend()

    plt.tight_layout()
    plt.savefig(f'{output_prefix}_jacks_plots.png', dpi=150)
    print(f'Saved plots to {output_prefix}_jacks_plots.png')

if __name__ == '__main__':
    # Example usage
    output_prefix = 'jacks_analysis'

    # Run analysis (assumes input files exist)
    # run_jacks_analysis('counts.txt', 'guidemap.txt', 'replicatemap.txt', output_prefix)

    # Analyze results
    gene_file = f'{output_prefix}_gene_JACKS_results.txt'
    guide_file = f'{output_prefix}_grna_JACKS_results.txt'

    try:
        genes, guides = analyze_results(gene_file, guide_file)
        plot_results(genes, guides, output_prefix)
    except FileNotFoundError:
        print('Run JACKS first to generate result files')
        print('Example: python run_JACKS.py counts.txt replicatemap.txt guidemap.txt --outprefix output')
