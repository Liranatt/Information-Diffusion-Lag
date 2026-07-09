import pandas as pd
import numpy as np

t3_f = pd.read_csv('data/experiment_forensics_clean/spy_t1_t2_t3_test_forensics.csv')
t4_f = pd.read_csv('data/experiment_forensics_clean/spy_t1_t2_t3_t4_test_forensics.csv')

print(f"T3 columns: {t3_f.columns}")

def print_forensics(df, name):
    print(f'--- {name} ---')
    print(df['decision'].value_counts())
    
    # Check skip reasons
    if 'skip_reason' in df.columns:
        print(df['skip_reason'].value_counts())
    
    # Check preemption reasons
    if 'preempt_reason' in df.columns:
        print(df['preempt_reason'].value_counts())
        
print_forensics(t3_f, 'T3')
print_forensics(t4_f, 'T4')
