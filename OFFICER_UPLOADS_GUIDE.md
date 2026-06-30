# Fulfillment Officer Upload Permissions & Features

## Overview
Fulfillment officers can upload **products, images, and bulk CSV data** to the Honey Cave Market platform. However, this permission must be explicitly enabled by an admin.

---

## 1. Enable Upload Permission for an Officer

### Location in Admin Panel
1. Go to **Admin Dashboard** → **Fulfillment Officers**
2. Click **Edit** on the officer you want to enable
3. In the form, check the box: **"Can upload products"**
4. Click **Save Changes**

### Form Field Details
- **Field Name**: `can_upload_products` (checkbox)
- **Database Table**: `mall_userprofile`
- **Column**: `can_upload_products` (BooleanField)
- **Default**: `False` (disabled by default)

### Backend File
- [mall/admin_views.py](mall/admin_views.py#L1200) — handles the edit form submission

### Template File
- [mall/templates/mall/admin/fulfillment_officer_form.html](mall/templates/mall/admin/fulfillment_officer_form.html) — the form where the checkbox appears

---

## 2. Single Product Upload (Officer Portal)

### Feature Description
Officers can add **one product at a time** with:
- Product name, description, price
- Category selection
- Up to 6 images (first image is primary, rest are gallery)

### How to Access
1. Log in as a fulfillment officer
2. Go to **Officer Portal** → **Upload Product**
3. Fill in the product details and upload images
4. Click **Add Product**

### URL
- `/fulfillment-officer/product/upload/`

### Backend Files
- [mall/fulfillment_officer_views.py](mall/fulfillment_officer_views.py#L790) — view handler (`officer_product_upload`)
- Permission check: `_officer_can_upload(user)` function

### Template Files
- `mall/templates/mall/fulfillment_officer/product_form.html` — the upload form
- The form checks permission: `{% if user.profile.can_upload_products %}`

---

## 3. Bulk CSV Upload (Officer Portal)

### Feature Description
Officers can **bulk import products** from a CSV file. The CSV can include:
- Product name, description, price, category
- Bulk image uploads for multiple products
- Pre-formatted data for quick onboarding

### How to Access
1. Log in as a fulfillment officer
2. Go to **Officer Portal** → **CSV Product Upload**
3. Download the CSV template (if available)
4. Fill in your products
5. Click **Upload CSV File**
6. Review the results (success/error report)

### URL
- `/fulfillment-officer/csv-upload/`

### Backend Files
- [mall/fulfillment_officer_views.py](mall/fulfillment_officer_views.py) — CSV upload handler
- Permission check: Same `can_upload_products` field

### Template Files
- `mall/templates/mall/fulfillment_officer/csv_upload.html` — the CSV upload interface

### CSV Format
The CSV must have headers like:
```
name,description,price,category
```

---

## 4. Limits & Validation

### Image Upload Limits
- **Max images per product**: 6 (1 primary + 5 gallery)
- **Max file size**: 5 MB per image
- **Allowed formats**: JPG, PNG, WebP, GIF
- **Image validation**: `validate_uploaded_image()` in [mall/security.py](mall/security.py)

### CSV Upload Limits
- **Max rows**: 1000 per file
- **Max file size**: 2 MB
- **Processing**: Server-side CSV validation and parsing in [mall/fulfillment_officer_views.py](mall/fulfillment_officer_views.py)

---

## 5. Admin CSV Upload (For Admins Only)

For reference, **admins** can also bulk upload via:
- **URL**: `/admin/csv-import/`
- **Files**: `mall/admin_views.py` — functions like `admin_csv_import_products()`
- **Types**: Products, Branches, Promo Codes
- **Same limits**: 1000 rows, 2 MB max

---

## 6. How Permission Checking Works

### In Views
```python
def _officer_can_upload(user):
    """Return True if this officer has been granted upload permission by admin."""
    try:
        return user.profile.can_upload_products
    except Exception:
        return False
```

### In Templates
```html
{% if user.profile and user.profile.can_upload_products %}
  <a href="{% url 'officer_product_upload' %}">Upload Product</a>
{% endif %}
```

The portal menu automatically hides upload links if the permission is `False`.

---

## 7. Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| Officer doesn't see "Upload Product" button | Check if `can_upload_products` is enabled in Admin → Officers → Edit |
| Image upload fails | Check file size (max 5 MB), format (JPG/PNG/WebP/GIF), and dimension |
| CSV upload fails | Verify CSV format matches template, check row count (max 1000), file size (max 2 MB) |
| Permission not updating | Ensure you clicked **Save Changes** after ticking the checkbox |

---

## 8. Key Files to Edit (If Customizing)

| File | Purpose |
|------|---------|
| [mall/models.py](mall/models.py#L123) | `UserProfile.can_upload_products` field definition |
| [mall/fulfillment_officer_views.py](mall/fulfillment_officer_views.py#L792) | Permission check function and upload handlers |
| [mall/templates/mall/admin/fulfillment_officer_form.html](mall/templates/mall/admin/fulfillment_officer_form.html) | Admin form where permission is toggled |
| [mall/templates/mall/fulfillment_officer/base.html](mall/templates/mall/fulfillment_officer/base.html#L137) | Portal menu that shows/hides upload links |
| [mall/admin_views.py](mall/admin_views.py#L1200) | Backend handler for saving permission changes |

---

## 9. Audit & Logging

All officer upload activities are logged in the **Audit Log** table:
- `actor`: The fulfillment officer who uploaded
- `action`: `product_upload`, `csv_upload`, etc.
- `target`: Product name or CSV filename
- `details`: Metadata (created/skipped/errors)
- `timestamp`: When the upload occurred

View audit logs: Admin → Audit Log → Filter by actor/action

---

