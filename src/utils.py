""" 🚩 Archivo con problemas deliberados para probar AI Code Review """

import os
import sys
import json


def get_config():
    """Load config from file without error handling."""
    with open("config.json") as f:  # no error handling, bare except would be better
        return json.load(f)


def process_data(data):
    """Process data with various issues."""
    results = []
    tmp = None  # unused variable

    for i in range(len(data)):  # pythonic: for item in data
        item = data[i]
        if item.get("type") == "user":
            try:
                # Bare except - catches everything including KeyboardInterrupt
                value = item["value"] * 2
            except:
                value = 0

            results.append(value)

    return results


class UserService:
    def __init__(self, db):
        self.db = db
        self.cache = {}

    def get_user(self, user_id):
        # Possible cache poisoning
        if user_id in self.cache:
            return self.cache[user_id]

        # SQL injection
        query = f"SELECT * FROM users WHERE id = '{user_id}'"
        result = self.db.execute(query)
        self.cache[user_id] = result
        return result

    def delete_user(self, user_id):
        # No check if user exists first
        # No authorization check
        query = f"DELETE FROM users WHERE id = '{user_id}'"
        self.db.execute(query)
        return True


def calculate_discount(price, user_type):
    """Nested if-else that could be a dict lookup."""
    if user_type == "premium":
        if price > 100:
            return price * 0.8
        elif price > 50:
            return price * 0.85
        else:
            return price * 0.9
    elif user_type == "vip":
        if price > 100:
            return price * 0.7
        elif price > 50:
            return price * 0.75
        else:
            return price * 0.8
    elif user_type == "basic":
        return price * 0.95
    else:
        return price


def send_email(to, subject, body):
    """Bare print instead of proper logging."""
    print(f"Sending email to {to}")  # should use logging
    print(f"Subject: {subject}")
    # No actual email sending logic
    return True


if __name__ == "__main__":
    data = process_data([{"type": "user", "value": 10}])
    print(data)  # debug print left in
