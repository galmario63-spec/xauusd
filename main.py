import time
import requests
TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6IjNjb0ZXIiX1c3cm1vZXiiXSwiaXNzIjoiTWV0YUFwaSIsImF1ZCI6WyJtZXRhYXBpIl0sInN1YiI6IjM5YWNlMmE3LThhNTMtNDIwZC05YWFiLWE5ODc3YmM0OGY1YyIsImV4cCI6MTgwMjAwNTI2M30.HrdfeF-F32sLQC89ekOu8Yqi4vJ8Y_UlHDTEEG-zkxK0TFKRi3j4o9SYmnl9e2skMjS-6Lk45J8YRyXyp-YaagagM1B_eJKrzBM09Yok2Ss1dUiM_VGr5npdLeglKijYN5Wdgqfa-hc4H3wq7Mnnxk5K3HgrHYkwb-tMGGnP1LRh6s61NhZfZVmd1jvgwRtEs6u6r0fMSJ05C-S8ewQoDQYy_Vsm7erWADoZnM2QA2NR5dwaxIHdN0D53i8pcOVv3H3U_9Hnk0TMFY8mlteQOJpSRmNnlsEtvcVv62TdRjcJt7SMSOjLqbFpIpHjBb9n9JQrg3HpMZGB83YFW-o_Pnxk3XnS0bcgI6A--Wy9i2NTcp1_OfMIm6TF0Tri46MxlFLG4-qyWLuPpUlDkzFfHgu9US6eRMQOBYNttQ21msyh-VzwujoIvCymNcObufEMBuQbhl0IiwdU9M2Fw1_-MobmdDz8iCUdduDECWX12Z42UHq-363KnjUjufaWNBqNrsFCdUhq9-1dRuH3iuUL6w9cCVtYh8Xi2-Gm9JpNweryZRIl65sFmPzyND-Vt2smTwo142xo8v-d3nKvwXQB2SxSvsh6bg-0SnqA-jFmpS16PNzM-kwl_QPzJUWJkZ2R8IccYV23rEB9vz7iQ-o78-LZPFNm-6S_U"
ACCOUNT_ID = "39ace2a7-8a53-420d-9aab-a9877bc48f5c"
SYMBOL = "XAUUSD"

headers = {
    "auth-token": TOKEN,
    "Content-Type": "application/json"
}

url = f"https://mt-client-api-v1.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}/symbols/{SYMBOL}/price"

print("Bot úspešne naštartovaný v cloude. Sledujem XAUUSD...")


