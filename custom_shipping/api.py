import frappe
import requests
import json
from datetime import datetime

# Shiprocket Credentials
site_config = frappe.get_site_config()
SHIPROCKET_EMAIL = site_config.get("shiprocket_email")
SHIPROCKET_PASSWORD = site_config.get("shiprocket_password")

SHIPROCKET_API_BASE = "https://apiv2.shiprocket.in/v1/external"

# Authenticate with Shiprocket
def get_shiprocket_token():
    url = f"{SHIPROCKET_API_BASE}/auth/login"
    payload = json.dumps({"email": SHIPROCKET_EMAIL, "password": SHIPROCKET_PASSWORD})
    headers = {"Content-Type": "application/json"}

    response = requests.post(url, data=payload, headers=headers)

    if response.status_code == 200:
        return response.json().get("token")
    else:
        frappe.throw(f"Shiprocket Authentication Failed: {response.text}")

@frappe.whitelist()
def create_shiprocket_order(doc=None, method=None):
    print("Starting Shiprocket Order Creation...")

    if not doc:
        frappe.throw("Sales Invoice document is required.")

    # Fetch linked Sales Order from Sales Invoice items
    sales_order_name = doc.items[0].sales_order if doc.items else None

    if not sales_order_name:
        frappe.throw("No linked Sales Order found in Sales Invoice.")

    print(f"Sales Order Found: {sales_order_name}")

    try:
        sales_order = frappe.get_doc("Sales Order", sales_order_name)
        print("Sales Order fetched successfully.")
    except Exception as e:
        print(f"Error fetching Sales Order: {str(e)}")
        frappe.throw(f"Error fetching Sales Order: {str(e)}")

    # Get Shiprocket Token
    token = get_shiprocket_token()
    print("Shiprocket token fetched successfully.")

    # Convert order date
    try:
        order_date = datetime.strptime(sales_order.transaction_date, "%Y-%m-%d") if isinstance(sales_order.transaction_date, str) else sales_order.transaction_date
    except Exception as e:
        print(f"Error parsing order date: {str(e)}")
        frappe.throw(f"Error parsing order date: {str(e)}")

    # Get billing address
    billing_address_name = sales_order.customer_address or frappe.get_value(
        "Address", {"links": {"link_doctype": "Customer", "link_name": sales_order.customer}}, "name"
    )

    if not billing_address_name:
        print(f"Billing address not found for customer {sales_order.customer}")
        frappe.throw(f"Billing address not found for customer {sales_order.customer}")

    try:
        billing_address_doc = frappe.get_doc("Address", billing_address_name)
        print(f"Billing address fetched: {billing_address_name}")
    except Exception as e:
        print(f"Error fetching billing address: {str(e)}")
        frappe.throw(f"Error fetching billing address: {str(e)}")

    # Extract customer name
    customer_name = frappe.get_value("Customer", sales_order.customer, "customer_name")
    name_parts = customer_name.strip().split(" ", 1)
    billing_customer_name = name_parts[0]
    billing_last_name = name_parts[1] if len(name_parts) > 1 else ""

    # Fetch order items
    order_items = [  
        {
            "name": item.item_name,
            "sku": item.item_code,
            "units": item.qty,
            "selling_price": item.rate
        }
        for item in sales_order.items
    ]

    if not order_items:
        print("No items found in the Sales Order.")
        frappe.throw("No items found in the Sales Order.")

    print(f"Order Items: {order_items}")
    
    length, width , height = calculate_order_dimensions(sales_order)
    # Order Data for Shiprocket
    order_data = {
        "order_id": sales_order.name,
        "order_date": order_date.strftime("%Y-%m-%d"),
        "channel_id": "",
        "billing_customer_name": billing_customer_name,
        "billing_last_name": billing_last_name,
        "billing_phone": billing_address_doc.phone,
        "billing_address": billing_address_doc.address_line1[:80],
        "billing_city": billing_address_doc.city,
        "billing_pincode": billing_address_doc.pincode,
        "billing_state": billing_address_doc.state,
        "billing_country": "India",
        "shipping_is_billing": 1,
        "order_items": order_items,
        "payment_method": "Prepaid",
        "sub_total": sales_order.total,
        "length": length,
        "breadth": width,
        "height": height,
        "weight": sales_order.total_net_weight / 1000
    }

    print(f"Shiprocket Order Data: {order_data}")

    # Call Shiprocket API
    url = "https://apiv2.shiprocket.in/v1/external/orders/create/adhoc"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

    try:
        response = requests.post(url, json=order_data, headers=headers)
        response_data = response.json()
        print(f"Shiprocket API Response: {response_data}")

        if response.status_code == 200 and response_data.get("order_id"):
            shiprocket_order_id = response_data.get("order_id")
            sales_order.db_set("custom_shiprocket_order_id", shiprocket_order_id)
            sales_order.db_set("custom_tracking_number", shiprocket_order_id)
            sales_order.db_set("custom_current_status", "Order Placed")
            frappe.msgprint(f"Order created in Shiprocket. Order ID: {shiprocket_order_id}")
            print(f"Shiprocket Order ID stored: {shiprocket_order_id}")
        else:
            print(f"Shiprocket Order creation failed. Response: {response.text}")
            frappe.throw(f"Shiprocket Order creation failed. Response: {response.text}")

    except Exception as e:
        print(f"Shiprocket API request failed: {str(e)}")
        frappe.throw(f"Shiprocket API request failed: {str(e)}")

    print("Shiprocket Order creation process completed successfully.")

def calculate_order_dimensions(sales_order):
    """Calculate and update the total dimensions (height, width, length) of the order"""
    if not sales_order:
        frappe.throw("Sales Order document is required.")

    total_length = 0
    total_width = 0
    total_height = 0

    for item in sales_order.items:
        item_length = frappe.get_value("Item", item.item_code, "custom_length") or 10
        item_width = frappe.get_value("Item", item.item_code, "custom_width") or 10
        item_height = frappe.get_value("Item", item.item_code, "custom_height") or 10

        total_length += item_length * item.qty
        total_width += item_width * item.qty
        total_height += item_height * item.qty

    # Save calculated values to custom fields in Sales Order
    sales_order.db_set("custom_total_length", total_length)
    sales_order.db_set("custom_total_width", total_width)
    sales_order.db_set("custom_total_height", total_height)

    return total_length, total_width, total_height



# Cancel an Order in Shiprocket
@frappe.whitelist()
def cancel_shiprocket_order(doc, method):
    if not doc.custom_shiprocket_order_id:
        frappe.throw("Order ID is required for cancellation.")

    token = get_shiprocket_token()
    url = f"{SHIPROCKET_API_BASE}/orders/cancel"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    payload = {"ids": [doc.custom_shiprocket_order_id]}  # Shiprocket expects a list of order IDs

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code == 200:
        frappe.msgprint(f"Order {doc.custom_shiprocket_order_id} cancelled successfully in Shiprocket.")
    else:
        frappe.throw(f"Failed to cancel order {doc.custom_shiprocket_order_id}. Response: {response.text}")




# Get Tracking Details
@frappe.whitelist(allow_guest=True)
def get_shiprocket_tracking(order_id):
    token = get_shiprocket_token()
    url = f"{SHIPROCKET_API_BASE}/courier/track?order_id={order_id}"
    headers = {"Authorization": f"Bearer {token}"}

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        frappe.throw(f"Tracking fetch failed: {response.text}")

# Generate Shipping Label
@frappe.whitelist()
def generate_shiprocket_label(order_id, order_data):
    token = get_shiprocket_token()
    url = f"{SHIPROCKET_API_BASE}/orders/print/labels"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    payload = {"ids": [order_id]}

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code == 200:
        label_url = response.json().get("label_url")
        frappe.msgprint(f"Shipping Label Generated: <a href='{label_url}' target='_blank'>Download Label</a>")
        return label_url
    else:
        frappe.throw(f"Failed to generate shipping label: {response.text}")

@frappe.whitelist(allow_guest=True)
def webhook_handler():
    try:
        # Get incoming JSON data
        data = json.loads(frappe.request.data or "{}")

        # Extract relevant fields
        shiprocket_order_id = data.get("order_id")  # This should match `custom_shiprocket_order_id`
        tracking_number = data.get("awb") or shiprocket_order_id
        estimated_delivery_date = data.get("etd")
        courier_partner = data.get("courier_name")
        current_status = data.get("current_status", "").lower()  # Normalize to lowercase

        if not shiprocket_order_id:
            frappe.log_error("Order ID missing in webhook data", "Shiprocket Webhook Error")
            return {"success": False, "message": "Order ID missing in webhook data"}

        # Find Sales Order using `custom_shiprocket_order_id`
        sales_order_name = frappe.db.get_value("Sales Order", 
                                               {"custom_shiprocket_order_id": shiprocket_order_id}, 
                                               "name")

        if not sales_order_name:
            frappe.log_error(f"Sales Order not found for Order ID: {shiprocket_order_id}", "Shiprocket Webhook Error")
            return {"success": False, "message": f"Sales Order not found for Order ID: {shiprocket_order_id}"}

        # ✅ Update tracking details in Sales Order (Even if submitted)
        frappe.db.set_value("Sales Order", sales_order_name, {
            "custom_tracking_number": tracking_number,
            "custom_estimated_delivery_date": estimated_delivery_date,
            "custom_courier_partner": courier_partner,
            "custom_current_status": current_status
        }, update_modified=True)

        # ✅ Ensure database commit after update
        frappe.db.commit()

        # ✅ If status is "delivered", create a Delivery Note
        if current_status == "delivered":
            frappe.log_error(f"Attempting to create Delivery Note for {sales_order_name}", "Shiprocket Webhook Debug")
            response = create_delivery_note(sales_order_name)
            return response

        return {"success": True, "message": f"Tracking updated for Sales Order {sales_order_name}"}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Shiprocket Webhook Error")
        return {"success": False, "error": str(e)}



def create_delivery_note(sales_order_name):
    try:
        # Fetch Sales Order details
        sales_order = frappe.get_doc("Sales Order", sales_order_name)

        # ✅ Check if Delivery Note already exists
        existing_dn = frappe.db.exists("Delivery Note", {"against_sales_order": sales_order_name})
        if existing_dn:
            return {"success": False, "message": f"Delivery Note already exists for Sales Order {sales_order_name}"}

        # ✅ Create a new Delivery Note with `ignore_permissions=True`
        delivery_note = frappe.get_doc({
            "doctype": "Delivery Note",
            "customer": sales_order.customer,
            "against_sales_order": sales_order.name,
            "posting_date": frappe.utils.today(),
            "set_warehouse": sales_order.items[0].warehouse,  # Assumes same warehouse for all items
            "items": [
                {
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "qty": item.qty,
                    "uom": item.uom,
                    "rate": item.rate,
                    "warehouse": item.warehouse
                } for item in sales_order.items
            ]
        })

        # ✅ Insert and Submit with Ignore Permissions
        delivery_note.insert(ignore_permissions=True)
        delivery_note.submit()

        # ✅ Mark Sales Order as "Completed"
        frappe.db.set_value("Sales Order", sales_order_name, "status", "Completed")

        frappe.db.commit()
        return {"success": True, "message": f"Delivery Note {delivery_note.name} created successfully"}

    except Exception as e:
        frappe.log_error(f"Error creating Delivery Note: {frappe.get_traceback()}", "Shiprocket Webhook Error")
        return {"success": False, "error": str(e)}
