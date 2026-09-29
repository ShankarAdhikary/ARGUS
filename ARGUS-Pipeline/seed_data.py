import json
import random
from faker import Faker

fake = Faker('en_IN')

def generate_firs(count=50):
    firs = []
    for _ in range(count):
        fir = {
            "fir_id": fake.uuid4(),
            "date": fake.date_this_year().isoformat(),
            "station": fake.city() + " Police Station",
            "complainant": fake.name(),
            "accused": fake.name(),
            "description": fake.text(max_nb_chars=200)
        }
        firs.append(fir)
    return firs

def generate_cdrs(count=50):
    cdrs = []
    for _ in range(count):
        cdr = {
            "call_id": fake.uuid4(),
            "caller": fake.phone_number(),
            "receiver": fake.phone_number(),
            "timestamp": fake.date_time_this_month().isoformat(),
            "duration_sec": random.randint(10, 3600)
        }
        cdrs.append(cdr)
    return cdrs

if __name__ == "__main__":
    fir_data = generate_firs(50)
    cdr_data = generate_cdrs(50)

    with open('synthetic_firs.json', 'w') as f:
        json.dump(fir_data, f, indent=4)

    with open('synthetic_cdrs.json', 'w') as f:
        json.dump(cdr_data, f, indent=4)