import re

sample_zero = '..."business":false,"feedback_count":0,"feedback_reputation":0,...'
sample_reviews = '..."business":false,"feedback_count":32,"feedback_reputation":0.98,...'

for s in [sample_zero, sample_reviews]:
    m_fb = re.search(r'"feedback_count":\s*(\d+)', s)
    m_rep = re.search(r'"feedback_reputation":\s*([\d\.]+)', s)
    sales = int(m_fb.group(1)) if m_fb else None
    rep = float(m_rep.group(1)) if m_rep else None
    rating = round(rep * 5.0, 1) if rep and rep > 0 else (0.0 if sales == 0 else None)
    print(f"Sales: {sales}, Rating: {rating}")
