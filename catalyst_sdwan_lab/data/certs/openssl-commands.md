# OpenSSL commands for Ent-CA

## Root CA

```
# Generate the Private Key
openssl genrsa -out rootCA.key 2048

# Generate the self-signed Root Cert
openssl req \
-new \
-x509 \
-days 3650 \
-key rootCA.key \
-config <( cat rootca_details.txt ) \
-out rootCA.pem
```

## Intermediate CA

```
# Generate the Private Key
openssl genrsa -out signCA.key 2048

# Generate the Certificate Signing Request
openssl req \
-new -sha256 \
-key signCA.key \
-out signCA.csr \
-config <( cat cacsr_details.txt )

# Issue the Intermediate signed CA
openssl x509 \
-req \
-days 3650 \
-in signCA.csr \
-CA rootCA.pem \
-CAkey rootCA.key \
-CAcreateserial \
-extensions v3_ext -extfile ./v3ext.cnf \
-out signCA.pem

# Prepare the CA cert chain
cat signCA.pem > chainCA.pem
cat rootCA.pem >> chainCA.pem
```