The error indicates that a container with the name `/pdf-api-container` already exists. Docker does not allow two containers to have the same name. You can resolve this issue by either removing the existing container or running the new container with a different name.

---

### **Option 1: Remove the Existing Container**
1. **Stop the Existing Container**:
   ```bash
   docker stop pdf-api-container
   ```

2. **Remove the Existing Container**:
   ```bash
   docker rm pdf-api-container
   ```

3. **Run the New Container**:
   After removing the old container, you can run the new container with the same name:
   ```bash
   docker run -d -p 5002:5002 \
     -e AWS_ACCESS_KEY_ID="mex1o5IQZUr4HI6p6NmOMVYUgZql9xF/xy3dX4oO" \
     -e AWS_SECRET_ACCESS_KEY="AKIAZQHXXJFS7HGCYI6R" \
     -e GEMINI_API_KEY="AIzaSyDmip6WwHwNmDekPTwCo8FK1nnc24Ifhv4" \
     -v /home/ec2-user/local_outputs:/app/local_outputs \
     --name pdf-api-container \
     pdf-processor-api
   ```

---

### **Option 2: Use a Different Name for the New Container**
If you want to keep the existing container, you can run the new container with a different name:

```bash
docker run -d -p 5002:5002 \
  -e AWS_ACCESS_KEY_ID="mex1o5IQZUr4HI6p6NmOMVYUgZql9xF/xy3dX4oO" \
  -e AWS_SECRET_ACCESS_KEY="AKIAZQHXXJFS7HGCYI6R" \
  -e GEMINI_API_KEY="AIzaSyDmip6WwHwNmDekPTwCo8FK1nnc24Ifhv4" \
  -v /home/ec2-user/local_outputs:/app/local_outputs \
  --name pdf-api-container-new \
  pdf-processor-api
```

---

### **Option 3: Check the Existing Container**
If you’re unsure about the state of the existing container, you can inspect it:

1. **List All Containers**:
   ```bash
   docker ps -a
   ```

   This will show all containers, including stopped ones.

2. **Restart the Existing Container** (if needed):
   If the existing container is stopped, you can restart it:
   ```bash
   docker start pdf-api-container
   ```

3. **Remove the Existing Container** (if no longer needed):
   ```bash
   docker rm pdf-api-container
   ```

---

### **Verify the Fix**
After resolving the conflict, verify that the container is running:

```bash
docker ps
```

You should see your container (`pdf-api-container` or the new name) in the list of running containers.

---

Let me know if you need further assistance!