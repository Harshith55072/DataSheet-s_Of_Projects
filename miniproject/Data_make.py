import json
import random
import uuid

# ==========================
# Base data for generation
# ==========================
roles = {
    "Frontend Developer": ["HTML", "CSS", "JavaScript", "React", "Vue", "Angular", "TypeScript"],
    "Backend Developer": ["Python", "Java", "Node.js", "Databases", "APIs", "Django", "Spring Boot"],
    "DevOps Engineer": ["Docker", "Kubernetes", "Jenkins", "AWS", "CI/CD", "Terraform"],
    "Machine Learning Engineer": ["Python", "TensorFlow", "PyTorch", "ML Deployment", "Data Preprocessing"],
    "Data Scientist": ["Python", "Pandas", "NumPy", "Scikit-learn", "Statistics", "Matplotlib"],
    "Data Engineer": ["ETL", "SQL", "Apache Spark", "Airflow", "BigQuery", "Hadoop"],
    "Cybersecurity Engineer": ["Network Security", "Firewalls", "Encryption", "Penetration Testing", "SIEM"],
    "Mobile Developer": ["Android", "Kotlin", "Swift", "iOS", "Flutter", "React Native"],
    "Cloud Engineer": ["AWS", "Azure", "Google Cloud", "Cloud Networking", "Serverless"],
    "Software Architect": ["System Design", "Microservices", "Scalability", "APIs", "Databases"]
}

categories = ["career_role", "career_advice", "learning_path", "interview_prep","meaning_explanation"]
levels = ["beginner", "intermediate", "advanced"]

# ==========================
# Generator Function
# ==========================
def generate_entry(role, skills, category, level):
    advice_templates = [
        f"As a {role}, focus on mastering {{skill}} to succeed at the {level} level.",
        f"{{skill}} is an essential technology for {role}. Study it thoroughly for {level}-level work.",
        f"To prepare for {role} interviews, practice solving problems related to {{skill}}.",
        f"A {level} {role} should be comfortable working with {{skill}} as part of daily tasks.",
        f"For a career in {role}, learning {{skill}} is highly recommended."
    ]
    
    skill = random.choice(skills)
    text = random.choice(advice_templates).format(skill=skill)
    
    return {
        "id": str(uuid.uuid4()),
        "text": text,
        "role": role,
        "skills": [skill],
        "category": category,
        "level": level
    }

# ==========================
# Build Knowledge Base
# ==========================
entries = []
target_size = 50000000

while len(entries) < target_size:
    for role, skills in roles.items():
        entry = generate_entry(
            role=role,
            skills=skills,
            category=random.choice(categories),
            level=random.choice(levels)
        )
        entries.append(entry)
        if len(entries) >= target_size:
            break

# ==========================
# Save to JSON
# ==========================
with open("software_career_knowledge.json", "w", encoding="utf-8") as f:
    json.dump(entries, f, indent=2, ensure_ascii=False)

print(f"✅ Generated {len(entries)} knowledge entries into software_career_knowledge.json")
