import os
import json
import argparse

# Define core products that serve as their own product_family
CORE_PRODUCTS = ["vault", "consul", "nomad", "packer", "boundary"]
# Products that belong to the terraform family
TERRAFORM_FAMILY = ["terraform", "sentinel"]

def generate_metadata(directory, bucket_name, output_file):
    metadata_entries = []
    
    print(f"Scanning directory: {directory}")
    
    for root, _, files in os.walk(directory):
        for file in files:
            # Only process relevant content types
            if file.endswith(('.md', '.tf', '.hcl', '.txt')) and file != 'metadata.jsonl':
                # Get the relative path from the base directory
                # Force strict string typing to prevent implicit TypeErrors
                safe_root = str(root)
                safe_file = str(file)
                safe_dir = str(directory)
                
                full_path = os.path.join(safe_root, safe_file)
                rel_path = full_path.replace(directory, "").lstrip("/")
                gcs_uri = f"gs://{bucket_name}/{rel_path}"
                
                # Extract the repository/folder name to determine product
                path_parts = rel_path.split(os.sep)
                repo_name = path_parts[0] if len(path_parts) > 1 else "general"
                repo_lower = repo_name.lower()

                # 1. Determine Product and Family
                product = repo_name
                product_family = "general"

                # Check if it is a core product (Consul, Nomad, Vault, etc.)
                core_match = next((c for c in CORE_PRODUCTS if c in repo_lower), None)
                
                if core_match:
                    product = repo_name
                    product_family = core_match # e.g., 'nomad'
                elif "terraform-provider" in repo_lower:
                    # Strip prefix for cleaner product name (e.g., 'aws')
                    product = repo_name.replace("terraform-provider-", "")
                    product_family = "terraform"
                elif any(tf in repo_lower for tf in TERRAFORM_FAMILY):
                    product = repo_name
                    product_family = "terraform"
                else:
                    product = repo_name
                    product_family = repo_name

                # 2. Source Type detection based on directory structure
                source_type = "official_docs"
                if "issues" in rel_path: 
                    source_type = "github_issue"
                elif "discuss" in rel_path: 
                    source_type = "discuss_forum"
                elif "blogs" in rel_path: 
                    source_type = "blog_post"

                # 3. Construct the JSONL entry
                entry = {
                    "gcs_uri": gcs_uri,
                    "metadata": {
                        "product": product,
                        "product_family": product_family,
                        "source_type": source_type,
                        "file_name": file
                    }
                }
                metadata_entries.append(json.dumps(entry))
    
    # Write the metadata mapping file
    with open(output_file, 'w') as f:
        f.write('\n'.join(metadata_entries))
        
    print(f"✅ Successfully generated metadata for {len(metadata_entries)} files.")
    print(f"📍 Output: {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate metadata.jsonl for Vertex AI RAG")
    parser.add_argument("--dir", default="/workspace/cleaned/", help="Directory containing processed files")
    parser.add_argument("--bucket", required=True, help="Target GCS bucket name")
    parser.add_argument("--output", default="/workspace/cleaned/metadata.jsonl", help="Output path for metadata.jsonl")
    
    args = parser.parse_args()
    
    # Run the generator
    if os.path.exists(args.dir):
        generate_metadata(args.dir, args.bucket, args.output)
    else:
        print(f"❌ Error: Directory {args.dir} does not exist.")