from flask import Flask, render_template, request, redirect, session, flash
from pymongo import MongoClient
import matplotlib.pyplot as plt
import os
import pickle
import numpy as np
import pandas as pd
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from collections import defaultdict
from bson.objectid import ObjectId
pipe=pickle.load(open("pipe.pkl",'rb'))
app = Flask(__name__)
app.secret_key = 'supersecretkey'

# MongoDB connection
client = MongoClient("mongodb://localhost:27017/")
db = client["walmart"]
stores_col = db["walmart_detail"]
all_items = db.inventory.find()
inventory_collection = db["inventory"]
# stores_collection = db["walmart_detail"]

for item in all_items:
    if item['status'] == 'surplus':
        existing = db.surplus_inventory.find_one({'_id': item['_id']})
        if existing:
            db.surplus_inventory.replace_one({'_id': item['_id']}, item)
        else:
            db.surplus_inventory.insert_one(item)

    elif item['status'] == 'less':
        existing = db.less_inventory.find_one({'_id': item['_id']})
        if existing:
            db.less_inventory.replace_one({'_id': item['_id']}, item)
        else:
            db.less_inventory.insert_one(item)

@app.route('/get_less_stores')
def get_less_stores():
    product_name = request.args.get("product_name")
    current_store_id = session.get("store_id")

    # Check if the logged-in store has this product as surplus
    current_item = inventory_collection.find_one({
        "product_name": product_name,
        "store_id": current_store_id
    })

    if not current_item or current_item.get("status") != "surplus":
        return jsonify([])  # Return empty list if not surplus

    # Now fetch all stores (excluding current) that need this product
    less_items = inventory_collection.find({
        "product_name": product_name,
        "status": "less"
    })

    store_needs = defaultdict(float)
    for item in less_items:
        store_id = item['store_id']
        if store_id == current_store_id:
            continue
        store_needs[store_id] += item.get('less_kg', 0)

    dropdown_options = []
    for store_id, qty in store_needs.items():
        store = stores_col.find_one({"_id": store_id})
        if store is None:
            continue
        label = f"{store.get('name', 'Unknown')} (needs {round(qty, 2)}kg)"
        dropdown_options.append({
            "store_id": store_id,
            "label": label
        })

    return jsonify(dropdown_options)
            
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        store_id = request.form.get("store_id")
        email = request.form.get("email")
        password = request.form.get("pass")

        try:
            password = int(password)
        except ValueError:
            flash("Password must be numeric.", "danger")
            return render_template("login.html")

        store = stores_col.find_one({
            "_id": store_id,
            "manager_email": email,
            "password": password,
            "active": True
        })

        if store:
            session['store_id'] = store['_id']
            session['region'] = store['region']
            # flash("Login successful!", "success")
            return redirect('/dashboard')
        else:
            flash("Invalid credentials or inactive store.", "danger")

    return render_template("login.html")

# @app.route('/dashboard')
# def dashboard():
#     if 'store_id' not in session:
#         flash("Please login first.", "warning")
#         return redirect('/login')
    
#     return f"Welcome, Store #{session['store_id']} in Region {session['region']}"
@app.route('/dashboard')
def dashboard():
    if 'store_id' not in session:
        flash("Please login first.", "warning")
        return redirect('/login')
    
    store_id = session['store_id']
    region = session['region']

    # Get inventory items for the logged-in store
    inventory_items = db["inventory"].find({"store_id": store_id})
    

    surplus_items = list(db.surplus_inventory.find({'store_id': store_id}))

    labels = [item['product_name'] for item in surplus_items]
    sizes = [item['surplus_kg'] for item in surplus_items]

    # Only generate chart if there are items
    if labels and sizes:
        fig, ax = plt.subplots()
        ax.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
        ax.axis('equal')  # Equal aspect ratio ensures the pie chart is circular.

        chart_path = os.path.join('static', 'surplus_pie.png')
        plt.savefig(chart_path)
        plt.close()
    else:
        chart_path = None

    return render_template("dashboard.html", 
                           store_id=store_id, 
                           region=region,
                           inventory=inventory_items,chart_path=chart_path)

@app.route('/active-requests')
def active_requests():
    if 'store_id' not in session:
        flash("Please login first.", "warning")
        return redirect('/login')

    current_store = session['store_id']
    incoming_requests = list(db.transfer_requests.find({
        "from_store": current_store,   # ✅ Now filtering by recipient
        "status": {"$in": ["Pending", "Rejected"]}
    }))

    # Format datetime to string
    for req in incoming_requests:
        if 'timestamp' in req:
            req['formatted_date'] = req['timestamp'].strftime('%Y-%m-%d %H:%M')

    return render_template("active_requests.html", requests=incoming_requests)

@app.route('/request-transfer', methods=['POST'])
def request_transfer():
    to_store = session['store_id']
    from_store = request.form['from_store']
    inventory_id = request.form['inventory_id']
    print(f"Received ID: '{inventory_id}'")  # Debug
    quantity_kg = float(request.form['quantity'])

    if from_store == to_store:
        flash("Cannot request transfer to the same store.", "warning")
        return redirect('/marketplace')

    # Fetch inventory item
    item = db.inventory.find_one({'_id': inventory_id})
    if not item:
        flash("Item not found.", "danger")
        return redirect('/marketplace')

    request_doc = {
        "product_name": item['product_name'],
        "category": item['category'],
        "quantity_kg": quantity_kg,
        "unit_price": item['unit_price'],
        "expiry_date": item['expiry_date'],
        "predicted_daily_sale_kg": item['predicted_daily_sale_kg'],
        "surplus_kg": item.get('surplus_kg', 0),
        "less_kg": item.get('less_kg', 0),
        "from_store": from_store,
        "to_store": to_store,
        "status": "Pending",
        "timestamp": datetime.now(),
        "region": item.get('region', 'Unknown')
    }

    db.transfer_requests.insert_one(request_doc)
    flash("Transfer request submitted.", "success")
    return redirect('/marketplace')


@app.route('/update-request', methods=['POST'])
def update_request():
    request_id = request.form['request_id']
    action = request.form['action']

    req = db.transfer_requests.find_one({"_id": ObjectId(request_id)})
    if not req:
        flash("Transfer request not found.", "danger")
        return redirect('/active-requests')

    db.transfer_requests.update_one(
        {"_id": ObjectId(request_id)},
        {"$set": {"status": action}}
    )

    if action == "Confirmed":
        from_store = req['from_store']
        to_store = req['to_store']
        quantity = float(req['quantity_kg'])
        product = req['product_name']
        
        # FROM store update
        from_item = db.inventory.find_one({
            "store_id": from_store,
            "product_name": product,
            "status": "surplus"
        })

        if from_item:
            new_surplus = float(from_item['surplus_kg']) - quantity
            new_quantity= float(from_item['quantity_kg']) - quantity
            if new_surplus <= 0:
                db.inventory.delete_one({"_id": from_item["_id"]})
                db.surplus_inventory.delete_one({"_id": from_item["_id"]})
            else:
                db.inventory.update_one(
                {"_id": from_item["_id"]},
                {"$set": {
                    "quantity_kg": new_quantity,
                    "surplus_kg": new_surplus
                }}
                )

                from_item['surplus_kg'] = new_surplus
                db.surplus_inventory.replace_one({"_id": from_item["_id"]}, from_item)

        # TO store update
        to_item = db.inventory.find_one({
            "store_id": to_store,
            "product_name": product
        })

        if to_item:
            new_quantity = float(to_item.get("quantity_kg", 0)) + quantity
            new_less = max(0, float(to_item.get("less_kg", 0)) - quantity)
            new_status = "less" if new_less > 0 else "adequate"

            db.inventory.update_one(
                {"_id": to_item["_id"]},
                {"$set": {
                    "quantity_kg": new_quantity,
                    "less_kg": new_less,
                    "status": new_status
                }}
            )

            to_item['quantity_kg'] = new_quantity
            to_item['less_kg'] = new_less
            to_item['status'] = new_status

            if new_status == "less":
                db.less_inventory.replace_one({"_id": to_item["_id"]}, to_item, upsert=True)
            else:
                db.less_inventory.delete_one({"_id": to_item["_id"]})

        else:
            new_id = f"inv_{str(len(list(db.inventory.find())) + 1).zfill(3)}"
            new_item = {
                "_id": new_id,
                "store_id": to_store,
                "product_name": req['product_name'],
                "category": req['category'],
                "quantity_kg": quantity,
                "unit_price": req['unit_price'],
                "expiry_date": req['expiry_date'],
                "predicted_daily_sale_kg": req['predicted_daily_sale_kg'],
                "region": req['region'],
                "date_added": datetime.now().strftime("%Y-%m-%d"),
                "status": "adequate",
                "less_kg": 0,
                "surplus_kg": 0
            }
            db.inventory.insert_one(new_item)

        flash("Transfer completed and inventory updated.", "success")
    else:
        flash(f"Request has been {action.lower()}.", "info")

    return redirect('/active-requests')


@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect('/login')

@app.route('/delete_item/<item_id>', methods=['POST'])
def delete_item(item_id):
    if 'store_id' not in session:
        flash("Please login first.", "warning")
        return redirect('/login')

    # Ensure the item exists and belongs to the logged-in store
    result = db.inventory.delete_one({
        "_id": item_id,
        "store_id": session['store_id']
    })

    if result.deleted_count > 0:
        pass
        # flash("Item deleted successfully.", "success")
    else:
        flash("Item not found or unauthorized deletion.", "danger")

    return redirect('/dashboard')

@app.route('/marketplace')
def marketplace():
    if 'store_id' not in session:
        flash("Please login first.", "warning")
        return redirect('/login')

    current_store_id = session['store_id']

    # Get all surplus items except those from current store
    surplus_items = list(db.inventory.find({
        'status': 'surplus',
        'store_id': {'$ne': current_store_id}
    }))

    # Add store info to each item
    for item in surplus_items:
        store = db.walmart_detail.find_one({'_id': item['store_id']})
        item["store_name"] = store.get("name", "Unknown") if store else "Unknown"
        item["region"] = store.get("region", "Unknown") if store else "Unknown"
        item["lat"] = store.get("location", {}).get("lat", "N/A")
        item["lng"] = store.get("location", {}).get("lng", "N/A")

    # ✅ Get all existing requests from current store
    existing_requests = list(db.transfer_requests.find({
        "from_store": current_store_id
    }))

    return render_template("marketplace.html", items=surplus_items, requests=existing_requests)

def predict_sale(pipe,region,product_name,quantity_kg,unit_price,expiry_date,local_event,temperature,rainfall):
        today = datetime.today()
        day_of_week = today.strftime('%A')
        date = datetime.strptime(expiry_date, "%Y-%m-%d")
        delta = (date - today).days


        query=pd.DataFrame([{
            'region': region,
            'item_name': product_name,
            'quantity_in_stock': quantity_kg,
            'price_per_kg': unit_price,
            'expiry_days': delta,
            'day_of_week': day_of_week,
            'local_event': local_event,
            'temperature':temperature,
            'rainfall': rainfall
        }])
        return pipe.predict(query)




@app.route('/add-inventory',methods=['GET', 'POST'])
def add():
    if request.method == 'POST':
        product_name = request.form.get("product_name")
        category = request.form.get("category")
        quantity_kg = request.form.get("quantity_kg")
        expiry_date = request.form.get("expiry_date")
        unit_price= request.form.get("unit_price")
        region= request.form.get("region")
        local_event=int(request.form.get("local_event"))
        temperature=request.form.get("temp")
        rainfall=request.form.get("rainfall")
        date_added=datetime.today()
        predicted_daily_sales=predict_sale(pipe,region,product_name,quantity_kg,unit_price,expiry_date,local_event,temperature,rainfall)
        today = datetime.today()
        day_of_week = today.strftime('%A')
        date = datetime.strptime(expiry_date, "%Y-%m-%d")
        delta = (date - today).days
        amount=(float(predicted_daily_sales[0]) * delta )- float(quantity_kg)
        if(amount<0.0):
            surplus_kg=abs(amount)
            less_kg=0.0
            status="surplus"
        else:
            less_kg=abs(amount)
            surplus_kg=0.0
            status="less"
        
        last_item = db.inventory.find_one(
            sort=[("_id", -1)],
            filter={"_id": {"$regex": "^inv_"}}
        )

        if last_item:
            last_id_num = int(last_item['_id'].split('_')[1])
            new_id = f"inv_{last_id_num + 1:03}"
        else:
            new_id = "inv_001"

        # Create inventory document
        item = {
            "_id": new_id,
            "store_id": session.get("store_id"),
            "product_name": product_name,
            "category": category,
            "quantity_kg": float(quantity_kg),
            "unit_price": float(unit_price),
            "expiry_date": expiry_date,
            "date_added": date_added.strftime("%Y-%m-%d"),
            "predicted_daily_sale_kg": float(predicted_daily_sales[0]),
            "region": region,
            "local_event": local_event,
            "surplus_kg": surplus_kg,
            "less_kg" : less_kg,
            "temperature": float(temperature),
            "rainfall": float(rainfall),
            "status": status
        }

        db.inventory.insert_one(item)

        flash(f"Inventory item {new_id} added successfully.", "success")
        return redirect('/dashboard')    
    return render_template("form.html")


if __name__ == '__main__':
    app.run(debug=True)
