# Import necessary libraries
import argparse
import json
import os
import re
import sys # For exit
import requests
from io import BytesIO
from PIL import Image, UnidentifiedImageError

# --- Core Image Processing Function (Resizing removed) ---
def process_image(base_img, mask_img, photo_img, position_info, is_swap=False, layer=0):
    """
    Processes and composites images based on mask and position.
    (Final resizing to 512px is REMOVED from this function)
    """
    print(f"Processing layer {layer} with position: {position_info}")
    try:
        # Ensure images are in RGBA for proper masking/compositing
        mask_img = mask_img.convert("RGBA")
        photo_img = photo_img.convert("RGBA")
        base_img = base_img.convert("RGBA")

        mask_size = mask_img.size
        photo_size = photo_img.size

        # Check for zero dimensions
        if mask_size[0] == 0 or mask_size[1] == 0 or photo_size[0] == 0 or photo_size[1] == 0:
            print(f"Warning: Zero dimension detected in mask ({mask_size}) or photo ({photo_size}). Skipping resize/crop for this layer.")
            scaled_photo = photo_img # Use original if dimensions are problematic
        else:
            # Resize photo to fit mask if necessary, then crop
            if mask_size[0] < photo_size[0] or mask_size[1] < photo_size[1]:
                # Calculate scale based on height primarily, or width if height is smaller
                scale = 1.0
                if photo_size[1] > 0 and mask_size[1] > 0:
                    scale = photo_size[1] / mask_size[1]
                    # If scaled width is still too small, scale by width instead
                    if int(photo_size[0] / scale) < mask_size[0] and photo_size[0] > 0 and mask_size[0] > 0:
                        scale = photo_size[0] / mask_size[0]
                elif photo_size[0] > 0 and mask_size[0] > 0: # If height is zero, try scaling by width
                    scale = photo_size[0] / mask_size[0]

                # Avoid division by zero or invalid scale
                if scale <= 0:
                    scale = 1.0

                new_width = int(photo_size[0] / scale)
                new_height = int(photo_size[1] / scale)

                # Ensure dimensions are not zero after scaling
                if new_width > 0 and new_height > 0:
                    print(f"Resizing photo from {photo_size} to ({new_width}, {new_height}) for mask {mask_size}")
                    scaled_photo = photo_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                else:
                    print("Warning: Scaled photo dimensions are zero. Using original photo.")
                    scaled_photo = photo_img
            else:
                scaled_photo = photo_img # No resize needed if photo fits within mask

            # Crop the potentially resized photo to the mask size
            print(f"Cropping photo to mask size: {mask_size}")
            scaled_photo = scaled_photo.crop((0, 0, mask_size[0], mask_size[1]))

        # Create masked photo layer
        masked_photo = Image.new("RGBA", mask_size, (0, 0, 0, 0)) # Ensure transparent background
        # Correct paste: Use the mask's alpha channel to blend the photo onto the transparent layer
        masked_photo.paste(scaled_photo, (0, 0), mask=mask_img)

        # Get position coordinates
        pos_x, pos_y = position_info[0], position_info[1]

        # Composite layers based on is_swap
        if is_swap:
            print("Swapping layers: Base on top")
            # Create a temporary layer for pasting the base on top
            temp_comp = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
            # Paste masked photo onto this temp layer at the correct position
            temp_comp.paste(masked_photo, (pos_x, pos_y), masked_photo) # Use masked_photo's alpha
            # Paste original base image on top of the photo layer
            temp_comp.paste(base_img, (0, 0), base_img) # Use base_img's alpha
            base_img = temp_comp # Update base_img with the result
        else:
            print("Standard layering: Photo on top")
            # Paste masked photo directly onto base image
            base_img.paste(masked_photo, (pos_x, pos_y), masked_photo) # Use masked_photo's alpha

        # Final resizing block is removed from this function

        return base_img

    except Exception as e:
        print(f"Error during image processing in layer {layer}: {e}")
        import traceback
        traceback.print_exc()
        return None

# --- Context Function and Loading Logic ---
def load_image(source):
    """Loads an image from a local path or URL."""
    img = None
    try:
        if os.path.exists(source):
            print(f"Loading image from path: {source}")
            img = Image.open(source)
        elif source.startswith('http://') or source.startswith('https://'):
            print(f"Downloading image from URL: {source}")
            headers = {'User-Agent': 'Mozilla/5.0'} # Some servers block default requests user-agent
            response = requests.get(source, stream=True, headers=headers, timeout=10)
            response.raise_for_status() # Raise an exception for bad status codes
            img = Image.open(BytesIO(response.content))
        else:
            print(f"Error: Source is not a valid path or URL: {source}")
            return None

        # Return a copy to avoid issues with modifying the original object later
        # Ensure image is loaded into memory, especially after download
        loaded_img = img.copy()
        img.close() # Close the file handle explicitly
        return loaded_img

    except FileNotFoundError:
        print(f"Error: File not found at path: {source}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error downloading image from URL {source}: {e}")
        return None
    except UnidentifiedImageError:
        print(f"Error: Cannot identify image file (may be corrupt or unsupported format): {source}")
        return None
    except Exception as e:
        print(f"Error loading image {source}: {e}")
        return None

def get_required_assets(template_id, positions_data, photo_paths, template_dir):
    """Determines required photos and masks based on config."""
    required_photo_indices = []
    required_mask_paths = []
    required_position_info = []
    current_id = template_id

    processed_ids = set() # To prevent infinite loops in config
    photo_needs = 0

    while current_id and current_id not in processed_ids:
        processed_ids.add(current_id)
        if current_id not in positions_data:
            print(f"Error: Template ID '{current_id}' not found in config positions.")
            return None, None, None # Indicate error

        position_info = positions_data[current_id]
        required_position_info.append(position_info)

        # Increment photo need and add index
        required_photo_indices.append(photo_needs)
        photo_needs += 1


        # Add current mask requirement
        mask_filename = f"mask{current_id}.png"
        mask_path = os.path.join(template_dir, mask_filename)
        if not os.path.exists(mask_path):
            print(f"Warning: Specific mask file not found: {mask_path}")
            # Attempt to use base template name convention if mask specific name not found
            # Heuristic: Try mask name matching the original template ID
            base_mask_filename = f"mask{template_id}.png"
            base_mask_path = os.path.join(template_dir, base_mask_filename)
            if os.path.exists(base_mask_path):
                 print(f"Using base mask file instead: {base_mask_path}")
                 required_mask_paths.append(base_mask_path)
            else:
                 print(f"Error: Base mask file also not found: {base_mask_path}. A mask file is required.")
                 return None, None, None
        else:
            required_mask_paths.append(mask_path)


        # Check if there's a next ID specified in the position data
        if len(position_info) > 2:
            next_id = str(position_info[2]) # Ensure it's a string for dict lookup
            # Sanity check: ensure the next ID exists in positions to avoid errors later
            if next_id not in positions_data:
                 print(f"Error: Referenced next ID '{next_id}' from '{current_id}' not found in config positions.")
                 return None, None, None
            current_id = next_id
        else:
            current_id = None # Stop the loop

    # Check if enough photos were provided
    if photo_needs > len(photo_paths):
        print(f"Error: Not enough profile photos provided for template '{template_id}'. Need {photo_needs}, got {len(photo_paths)}.")
        return None, None, None

    # Select the required photos based on indices
    final_photo_paths = [photo_paths[i] for i in required_photo_indices]

    return final_photo_paths, required_mask_paths, required_position_info

# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Composite profile pictures onto templates using masks.")
    # Removed --detect-position and --id arguments
    parser.add_argument("--template", required=True, help="Path to the base template image (e.g., 'templates/eatID.png'). The ID is extracted from the filename.")
    parser.add_argument("--profilephoto", required=True, nargs='+', help="Path(s) or URL(s) to the profile photo(s). Provide in order needed by template.")
    parser.add_argument("--output", required=True, help="Path to save the final composited image.")
    parser.add_argument("--config", default="config.json", help="Path to the configuration JSON file (default: config.json).")
    parser.add_argument("--swap", action='store_true', help="Use swapped layering (base image on top).")

    args = parser.parse_args()

    # --- Processing Mode --- (No mode selection needed anymore)
    print("--- Running in Image Processing Mode ---")

    # --- Load Configuration ---
    try:
        positions = {}
        if os.path.exists(args.config):
            with open(args.config, 'r', encoding='utf-8') as f:
                try:
                    config_data = json.load(f)
                    positions = config_data.get("positions", {})
                except json.JSONDecodeError:
                     print(f"Warning: Config file {args.config} contains invalid JSON. Proceeding without positions.")
        else:
             print(f"Info: Config file {args.config} not found. Proceeding without pre-defined positions.")

        if not positions:
            print(f"Warning: 'positions' data not found or empty in {args.config}")
            # Depending on use case, you might want to exit if config is essential
            # sys.exit(1)
    except Exception as e:
        print(f"Error loading config file {args.config}: {e}")
        sys.exit(1)

    # --- Determine Template ID and Paths ---
    template_path = args.template
    template_dir = os.path.dirname(template_path)
    if not template_dir: # Handle case where template is in current directory
        template_dir = "."
    template_filename = os.path.basename(template_path)

    # Extract ID (e.g., 'ri', 'zou', 'ada') from filename like 'eatID.png'
    match = re.match(r"eat([a-zA-Z0-9_]+)\.png", template_filename, re.IGNORECASE)
    if not match:
        print(f"Error: Could not extract template ID from filename: {template_filename}. Expected format like 'eatID.png'.")
        sys.exit(1)
    template_id = match.group(1).lower() # Use lowercase for consistency
    print(f"Using Template ID: {template_id}")

    # Check if template ID exists in loaded positions
    if template_id not in positions:
         print(f"Error: Template ID '{template_id}' extracted from filename is not defined in the 'positions' section of {args.config}.")
         print("Available IDs:", list(positions.keys()))
         sys.exit(1)

    # --- Determine Required Photos and Masks ---
    req_photo_sources, req_mask_paths, req_positions = get_required_assets(
        template_id, positions, args.profilephoto, template_dir
    )

    if req_photo_sources is None:
        sys.exit(1) # Error message already printed

    print(f"Required photos sources: {req_photo_sources}")
    print(f"Required masks paths: {req_mask_paths}")
    print(f"Processing positions: {req_positions}")

    # --- Load Base and Asset Images ---
    base_image = load_image(template_path)
    if base_image is None: sys.exit(1)

    profile_images = [load_image(src) for src in req_photo_sources]
    if any(img is None for img in profile_images):
        print("Error loading one or more profile photos.")
        sys.exit(1)

    mask_images = [load_image(path) for path in req_mask_paths]
    if any(img is None for img in mask_images):
        print("Error loading one or more mask images.")
        sys.exit(1)

    # --- Perform Image Processing Iteratively ---
    current_base = base_image.copy() # Start with a copy of the base

    # Process each required photo/mask/position set sequentially
    for i in range(len(req_photo_sources)):
         current_photo = profile_images[i]
         current_mask = mask_images[i]
         current_position = req_positions[i] # The list like [x, y] or [x, y, "next_id"]

         print(f"\nProcessing step {i+1} using photo {i+1} and mask {i+1}")
         processed_base = process_image(
             current_base,
             current_mask,
             current_photo,
             current_position, # Pass the whole position info list
             args.swap,
             layer=i # Use index as layer indicator
         )

         if processed_base is None:
             print(f"Error occurred during processing step {i+1}. Aborting.")
             sys.exit(1)
         else:
             current_base = processed_base # Update the base for the next iteration

    # --- Apply Final Resize AFTER loop ---
    print("\nApplying final resize to 512px max dimension...")
    final_image = current_base # Start with the fully composited image
    temp_dim = max(final_image.size[0], final_image.size[1])
    if temp_dim != 512 and temp_dim > 0:
        scale_final = 512 / temp_dim
        final_width = int(final_image.size[0] * scale_final)
        final_height = int(final_image.size[1] * scale_final)
        if final_width > 0 and final_height > 0:
             final_image = final_image.resize(
                 (final_width, final_height),
                 Image.Resampling.LANCZOS,
             )
             print(f"Resized final image to ({final_width}, {final_height})")
        else:
             print("Warning: Final calculated dimensions for resize are zero, skipping.")
    elif temp_dim == 0:
        print("Warning: Image has zero dimension before final resize.")
    else:
        print("Final image already has max dimension 512px. No resize needed.")

    # --- Save Result ---
    try:
        output_format = os.path.splitext(args.output)[1][1:].upper()
        if not output_format: output_format = "PNG"
        if output_format == "JPG": output_format = "JPEG"

        print(f"\nSaving final image to: {args.output} (Format: {output_format})")
        output_dir = os.path.dirname(args.output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            print(f"Created output directory: {output_dir}")

        if output_format == 'JPEG':
             print("Output format is JPEG, converting to RGB...")
             # Create a white background and paste the RGBA image onto it
             bg = Image.new("RGB", final_image.size, (255, 255, 255))
             # Ensure alpha channel exists before splitting
             if final_image.mode == 'RGBA':
                 bg.paste(final_image, mask=final_image.split()[3]) # Use alpha channel as mask
             else:
                 bg.paste(final_image) # Paste directly if no alpha
             final_image = bg

        final_image.save(args.output, format=output_format)
        print("Image processing complete.")

    except ValueError as e:
         print(f"Error saving output image to {args.output}: Unsupported format '{output_format}'? Full error: {e}")
         sys.exit(1)
    except Exception as e:
        print(f"Error saving output image to {args.output}: {e}")
        sys.exit(1)