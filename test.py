from minio import Minio

minio_client = Minio(
    "127.0.0.1:9000",
    access_key="admin",
    secret_key="Admin@123456",
    secure=False
)

bucket_name = "photo-bucket"
exists = minio_client.bucket_exists(bucket_name)
print(f"bucket是否存在: {exists}")

if not exists:
    minio_client.make_bucket(bucket_name)
    print("bucket创建成功")
else:
    print("bucket已经存在，无需创建")

